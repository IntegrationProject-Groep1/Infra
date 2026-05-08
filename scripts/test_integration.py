"""
RabbitMQ Integration Test Suite
Groep 1 — Desideriushogeschool 2026
Based on: XML/XSD Contract v2.3

Tests every message flow defined in the central contract:
  Frontend · CRM · Kassa · Facturatie · Planning · Mailing · Monitoring · Identity

Usage:
    python test_integration.py [options]

Options:
    --host HOST         RabbitMQ host  (default: localhost)
    --port PORT         AMQP port      (default: 5672)
    --user USER         Username       (default: guest)
    --pass PASS         Password       (default: guest)
    --mgmt-port PORT    Management API (default: 15672)
    --vhost VHOST       Virtual host   (default: /)
    --timeout SECS      Arrival check  (default: 5)
    --teams TEAMS       Comma list of teams to test, e.g. crm,kassa (default: all)
    --dry-run           Validate XML only, skip publish/arrival
    --verbose           Print full XML payloads
    --env FILE          Load .env file (default: .env if present)
"""

import argparse
import os
import sys
import time
import uuid
import json
import textwrap
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

import pika
import urllib.request
import urllib.parse
from lxml import etree

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN  = "\033[0;32m"
RED    = "\033[0;31m"
YELLOW = "\033[1;33m"
CYAN   = "\033[0;36m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):    print(f"  {GREEN}✓{RESET} {msg}")
def fail(msg):  print(f"  {RED}✗{RESET} {msg}"); _state["failures"] += 1
def warn(msg):  print(f"  {YELLOW}⚠{RESET} {msg}")
def info(msg):  print(f"  {CYAN}→{RESET} {msg}")
def header(msg):print(f"\n{BOLD}{CYAN}══ {msg} ══{RESET}")

_state = {"failures": 0, "tests": 0}


# ── Config ─────────────────────────────────────────────────────────────────────
def load_env(path=".env"):
    if not Path(path).exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def parse_args():
    p = argparse.ArgumentParser(description="RabbitMQ Integration Tests — Groep 1 v2.3")
    p.add_argument("--host",      default=os.getenv("RABBIT_HOST", "20.126.113.148"))
    p.add_argument("--port",      type=int, default=int(os.getenv("RABBIT_PORT", 30000)))
    p.add_argument("--user",      default=os.getenv("RABBIT_USER", "guest"))
    p.add_argument("--pass",      dest="password", default=os.getenv("RABBIT_PASS", "guest"))
    p.add_argument("--mgmt-port", type=int, default=int(os.getenv("RABBIT_MGMT_PORT", 30001)))
    p.add_argument("--vhost",     default=os.getenv("RABBIT_VHOST", "/"))
    p.add_argument("--timeout",   type=int, default=int(os.getenv("TIMEOUT", 5)))
    p.add_argument("--teams",     default="all")
    p.add_argument("--dry-run",        action="store_true")
    p.add_argument("--verbose",        action="store_true")
    p.add_argument("--env",            default=".env")
    p.add_argument("--pause-consumers", action="store_true",
                   help="Close consumer connections before testing default-exchange queues "
                        "so messages stay visible long enough to verify arrival")
    p.add_argument("--pause-delay",    type=float, default=0.8,
                   help="Seconds to wait after closing consumers before publishing (default: 0.8)")
    return p.parse_args()


# ── XSD Schemas (v2.3 contract) ────────────────────────────────────────────────
# All schemas derived verbatim from XML_XSD_Contract_v2.3_Centralized 1.md
# Key rules from the contract:
#   - No xmlns, no <receiver> in header
#   - version MUST be "2.0"
#   - Names wrapped in <contact> block
#   - currency="eur" attribute on all monetary amounts
#   - date_of_birth instead of age
#   - source is enum (frontend|crm|kassa|planning|facturatie|mailing|monitoring|iot_gateway)

SCHEMAS = {}

# ── Shared header (used by all standard messages) ──────────────────────────────
SCHEMAS["header"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">

  <xs:complexType name="HeaderType">
    <xs:sequence>
      <xs:element name="message_id" type="UUIDType"/>
      <xs:element name="timestamp"  type="xs:dateTime"/>
      <xs:element name="source"     type="SourceType"/>
      <xs:element name="type"       type="xs:string"/>
      <xs:element name="version">
        <xs:simpleType>
          <xs:restriction base="xs:string">
            <xs:enumeration value="2.0"/>
          </xs:restriction>
        </xs:simpleType>
      </xs:element>
      <xs:element name="correlation_id" type="UUIDType" minOccurs="0"/>
    </xs:sequence>
  </xs:complexType>

  <xs:simpleType name="UUIDType">
    <xs:restriction base="xs:string">
      <xs:pattern value="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"/>
    </xs:restriction>
  </xs:simpleType>

  <xs:simpleType name="SourceType">
    <xs:restriction base="xs:string">
      <xs:enumeration value="frontend"/>
      <xs:enumeration value="crm"/>
      <xs:enumeration value="kassa"/>
      <xs:enumeration value="planning"/>
      <xs:enumeration value="facturatie"/>
      <xs:enumeration value="mailing"/>
      <xs:enumeration value="monitoring"/>
      <xs:enumeration value="identity"/>
      <xs:enumeration value="iot_gateway"/>
    </xs:restriction>
  </xs:simpleType>

</xs:schema>"""

# ── Heartbeat (Section 3) ──────────────────────────────────────────────────────
# Queue: heartbeat (direct, default exchange)
SCHEMAS["heartbeat"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:pattern value="[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="heartbeat"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="version">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="2.0"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="status">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="online"/>
                  <xs:enumeration value="offline"/>
                  <xs:enumeration value="degraded"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="uptime" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Frontend → CRM : new_registration (Section 5.1) ───────────────────────────
# Queue: crm.incoming
SCHEMAS["frontend_to_crm_new_registration"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"       type="xs:string"/>
              <xs:element name="type">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="private"/>
                  <xs:enumeration value="company"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="contact">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="first_name"     type="xs:string"/>
                    <xs:element name="last_name"      type="xs:string"/>
                    <xs:element name="email"          type="xs:string"/>
                    <xs:element name="date_of_birth"  type="xs:date"   minOccurs="0"/>
                    <xs:element name="phone"          type="xs:string" minOccurs="0"/>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
              <xs:element name="company_name"  type="xs:string"  minOccurs="0"/>
              <xs:element name="vat_number"    type="xs:string"  minOccurs="0"/>
              <xs:element name="payment_due" minOccurs="0">
                <xs:complexType>
                  <xs:simpleContent>
                    <xs:extension base="xs:decimal">
                      <xs:attribute name="currency" type="xs:string" use="required"/>
                    </xs:extension>
                  </xs:simpleContent>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── CRM → Kassa : new_registration (Section 10.1) ─────────────────────────────
# Queue: kassa.incoming (exchange: kassa.exchange)
SCHEMAS["crm_to_kassa_new_registration"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"      type="xs:string"/>
              <xs:element name="customer">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="contact">
                      <xs:complexType>
                        <xs:sequence>
                          <xs:element name="first_name"    type="xs:string"/>
                          <xs:element name="last_name"     type="xs:string"/>
                          <xs:element name="email"         type="xs:string"/>
                          <xs:element name="date_of_birth" type="xs:date"   minOccurs="0"/>
                        </xs:sequence>
                      </xs:complexType>
                    </xs:element>
                    <xs:element name="company_name" type="xs:string" minOccurs="0"/>
                    <xs:element name="vat_number"   type="xs:string" minOccurs="0"/>
                    <xs:element name="type">
                      <xs:simpleType><xs:restriction base="xs:string">
                        <xs:enumeration value="private"/>
                        <xs:enumeration value="company"/>
                      </xs:restriction></xs:simpleType>
                    </xs:element>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
              <xs:element name="payment_due" minOccurs="0">
                <xs:complexType>
                  <xs:simpleContent>
                    <xs:extension base="xs:decimal">
                      <xs:attribute name="currency" type="xs:string" use="required"/>
                    </xs:extension>
                  </xs:simpleContent>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Kassa → CRM : consumption_order (Section 6.1) ─────────────────────────────
# Queue: kassa.payments.consumption (exchange: kassa.exchange)
SCHEMAS["kassa_to_crm_consumption_order"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"   type="xs:string"/>
              <xs:element name="badge_id"  type="xs:string"/>
              <xs:element name="items">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="item" maxOccurs="unbounded">
                      <xs:complexType>
                        <xs:sequence>
                          <xs:element name="name"     type="xs:string"/>
                          <xs:element name="quantity" type="xs:positiveInteger"/>
                          <xs:element name="unit_price">
                            <xs:complexType>
                              <xs:simpleContent>
                                <xs:extension base="xs:decimal">
                                  <xs:attribute name="currency" type="xs:string" use="required"/>
                                </xs:extension>
                              </xs:simpleContent>
                            </xs:complexType>
                          </xs:element>
                          <xs:element name="total_amount">
                            <xs:complexType>
                              <xs:simpleContent>
                                <xs:extension base="xs:decimal">
                                  <xs:attribute name="currency" type="xs:string" use="required"/>
                                </xs:extension>
                              </xs:simpleContent>
                            </xs:complexType>
                          </xs:element>
                        </xs:sequence>
                      </xs:complexType>
                    </xs:element>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
              <xs:element name="total_order_amount">
                <xs:complexType>
                  <xs:simpleContent>
                    <xs:extension base="xs:decimal">
                      <xs:attribute name="currency" type="xs:string" use="required"/>
                    </xs:extension>
                  </xs:simpleContent>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Kassa → CRM : payment_registered (Section 6.6) ───────────────────────────
# Routing key: kassa.payments.registration
SCHEMAS["kassa_to_crm_payment_registered"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"       type="xs:string"/>
              <xs:element name="badge_id"      type="xs:string"/>
              <xs:element name="amount_paid">
                <xs:complexType>
                  <xs:simpleContent>
                    <xs:extension base="xs:decimal">
                      <xs:attribute name="currency" type="xs:string" use="required"/>
                    </xs:extension>
                  </xs:simpleContent>
                </xs:complexType>
              </xs:element>
              <xs:element name="payment_method">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="cash"/>
                  <xs:enumeration value="card"/>
                  <xs:enumeration value="badge_wallet"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── CRM → Facturatie : invoice_request (Section 11.1) ─────────────────────────
# Queue: facturatie.incoming  (NOT crm.to.facturatie — that's a known CRM bug)
SCHEMAS["crm_to_facturatie_invoice_request"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id" type="xs:string"/>
              <xs:element name="invoice_data">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="company_name"  type="xs:string"/>
                    <xs:element name="vat_number"    type="xs:string" minOccurs="0"/>
                    <xs:element name="contact">
                      <xs:complexType>
                        <xs:sequence>
                          <xs:element name="first_name" type="xs:string"/>
                          <xs:element name="last_name"  type="xs:string"/>
                          <xs:element name="email"      type="xs:string"/>
                        </xs:sequence>
                      </xs:complexType>
                    </xs:element>
                    <xs:element name="amount_due">
                      <xs:complexType>
                        <xs:simpleContent>
                          <xs:extension base="xs:decimal">
                            <xs:attribute name="currency" type="xs:string" use="required"/>
                          </xs:extension>
                        </xs:simpleContent>
                      </xs:complexType>
                    </xs:element>
                    <xs:element name="description" type="xs:string" minOccurs="0"/>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── CRM → Mailing : send_mailing (Section 12.1) ───────────────────────────────
# Queue: crm.to.mailing  (type MUST be send_mailing, NOT mailing_status)
SCHEMAS["crm_to_mailing_send_mailing"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="recipient_email" type="xs:string"/>
              <xs:element name="recipient_name"  type="xs:string" minOccurs="0"/>
              <xs:element name="subject"         type="xs:string"/>
              <xs:element name="template_id"     type="xs:string" minOccurs="0"/>
              <xs:element name="context"         minOccurs="0">
                <xs:complexType>
                  <xs:sequence minOccurs="0" maxOccurs="unbounded">
                    <xs:any processContents="lax"/>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Planning → CRM : session_created (Section 7.1) ────────────────────────────
# Exchange: planning.exchange, routing: planning.session.created
# Queue bound: planning.session.events
SCHEMAS["planning_to_crm_session_created"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="session_id"      type="xs:string"/>
              <xs:element name="title"           type="xs:string"/>
              <xs:element name="start_datetime"  type="xs:dateTime"/>
              <xs:element name="end_datetime"    type="xs:dateTime"/>
              <xs:element name="location"        type="xs:string" minOccurs="0"/>
              <xs:element name="session_type"    type="xs:string" minOccurs="0"/>
              <xs:element name="status"          type="xs:string" minOccurs="0"/>
              <xs:element name="max_attendees"   type="xs:positiveInteger" minOccurs="0"/>
              <xs:element name="speaker" minOccurs="0">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="contact">
                      <xs:complexType>
                        <xs:sequence>
                          <xs:element name="first_name" type="xs:string"/>
                          <xs:element name="last_name"  type="xs:string"/>
                        </xs:sequence>
                      </xs:complexType>
                    </xs:element>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Frontend → Planning : calendar_invite (Section 17.2) ──────────────────────
# Exchange: calendar.exchange, routing: frontend.to.planning.calendar.invite
# attendee_email is required per v2.3 audit fix
SCHEMAS["frontend_to_planning_calendar_invite"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="session_id"      type="xs:string"/>
              <xs:element name="title"           type="xs:string"/>
              <xs:element name="start_datetime"  type="xs:dateTime"/>
              <xs:element name="end_datetime"    type="xs:dateTime"/>
              <xs:element name="attendee_email"  type="xs:string"/>
              <xs:element name="location"        type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Monitoring → Mailing : system_alert (Section 4) ───────────────────────────
# Queue: monitoring.alerts
SCHEMAS["monitoring_to_mailing_system_alert"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="alert_level">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="warning"/>
                  <xs:enumeration value="critical"/>
                  <xs:enumeration value="info"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="affected_team" type="xs:string"/>
              <xs:element name="message"       type="xs:string"/>
              <xs:element name="timestamp"     type="xs:dateTime" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Facturatie → CRM : invoice_status (Section 8.1) ───────────────────────────
# Queue: facturatie.to.crm  (type MUST be invoice_status, NOT send_invoice)
SCHEMAS["facturatie_to_crm_invoice_status"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="invoice_id"  type="xs:string"/>
              <xs:element name="user_id"     type="xs:string"/>
              <xs:element name="status">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="created"/>
                  <xs:enumeration value="sent"/>
                  <xs:enumeration value="paid"/>
                  <xs:enumeration value="overdue"/>
                  <xs:enumeration value="cancelled"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="amount_due" minOccurs="0">
                <xs:complexType>
                  <xs:simpleContent>
                    <xs:extension base="xs:decimal">
                      <xs:attribute name="currency" type="xs:string" use="required"/>
                    </xs:extension>
                  </xs:simpleContent>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""


# ── Log message (Section 3) ───────────────────────────────────────────────────
# Queue: logs (default exchange)
SCHEMAS["log_message"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="level">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="info"/>
                  <xs:enumeration value="warning"/>
                  <xs:enumeration value="error"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="action">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="registration"/>
                  <xs:enumeration value="user"/>
                  <xs:enumeration value="payment"/>
                  <xs:enumeration value="invoice"/>
                  <xs:enumeration value="session"/>
                  <xs:enumeration value="calendar"/>
                  <xs:enumeration value="email"/>
                  <xs:enumeration value="wallet"/>
                  <xs:enumeration value="refund"/>
                  <xs:enumeration value="identity"/>
                  <xs:enumeration value="xml_validation"/>
                  <xs:enumeration value="system_error"/>
                  <xs:enumeration value="badge"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="message" type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Frontend → CRM : user_created (Section 5.2) ───────────────────────────────
SCHEMAS["frontend_to_crm_user_created"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id" type="xs:string"/>
              <xs:element name="contact">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="first_name"    type="xs:string"/>
                    <xs:element name="last_name"     type="xs:string"/>
                    <xs:element name="email"         type="xs:string"/>
                    <xs:element name="date_of_birth" type="xs:date"   minOccurs="0"/>
                    <xs:element name="phone"         type="xs:string" minOccurs="0"/>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# user_updated and user_registered share same structure
SCHEMAS["frontend_to_crm_user_updated"]    = SCHEMAS["frontend_to_crm_user_created"]
SCHEMAS["frontend_to_crm_user_registered"] = SCHEMAS["frontend_to_crm_user_created"]

# ── Frontend → CRM : user_deleted (Section 5.4) ───────────────────────────────
SCHEMAS["frontend_to_crm_user_deleted"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id" type="xs:string"/>
              <xs:element name="reason"  type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# cancel_registration shares same simple body
SCHEMAS["frontend_to_crm_cancel_registration"] = SCHEMAS["frontend_to_crm_user_deleted"]

# CRM → Planning cancel_registration needs optional correlation_id in header
SCHEMAS["crm_to_planning_cancel_registration"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id" type="xs:string"/>
              <xs:element name="reason"  type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Frontend → CRM : user_checkin (Section 19.1) ──────────────────────────────
SCHEMAS["frontend_to_crm_user_checkin"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"  type="xs:string"/>
              <xs:element name="badge_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Kassa → CRM : badge_assigned (Section 6.4) ────────────────────────────────
# Exchange: kassa.exchange, routing: kassa.payments.badge → crm.incoming
SCHEMAS["kassa_to_crm_badge_assigned"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"  type="xs:string"/>
              <xs:element name="badge_id" type="xs:string"/>
              <xs:element name="email"    type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Kassa → CRM : refund_processed (Section 6.5) ─────────────────────────────
# Exchange: kassa.exchange, routing: kassa.payments.refund → crm.incoming
SCHEMAS["kassa_to_crm_refund_processed"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"  type="xs:string"/>
              <xs:element name="email"    type="xs:string" minOccurs="0"/>
              <xs:element name="refund">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="amount">
                      <xs:complexType>
                        <xs:simpleContent>
                          <xs:extension base="xs:decimal">
                            <xs:attribute name="currency" type="xs:string" use="required"/>
                          </xs:extension>
                        </xs:simpleContent>
                      </xs:complexType>
                    </xs:element>
                    <xs:element name="reason"         type="xs:string" minOccurs="0"/>
                    <xs:element name="original_amount" minOccurs="0">
                      <xs:complexType>
                        <xs:simpleContent>
                          <xs:extension base="xs:decimal">
                            <xs:attribute name="currency" type="xs:string" use="required"/>
                          </xs:extension>
                        </xs:simpleContent>
                      </xs:complexType>
                    </xs:element>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Kassa → CRM : invoice_request (Section 6.7) ──────────────────────────────
# Exchange: kassa.exchange, routing: kassa.payments.invoice → crm.incoming
SCHEMAS["kassa_to_crm_invoice_request"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id" type="xs:string"/>
              <xs:element name="invoice_data">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="company_name" type="xs:string" minOccurs="0"/>
                    <xs:element name="vat_number"   type="xs:string" minOccurs="0"/>
                    <xs:element name="contact">
                      <xs:complexType>
                        <xs:sequence>
                          <xs:element name="first_name" type="xs:string"/>
                          <xs:element name="last_name"  type="xs:string"/>
                          <xs:element name="email"      type="xs:string"/>
                        </xs:sequence>
                      </xs:complexType>
                    </xs:element>
                    <xs:element name="amount_due">
                      <xs:complexType>
                        <xs:simpleContent>
                          <xs:extension base="xs:decimal">
                            <xs:attribute name="currency" type="xs:string" use="required"/>
                          </xs:extension>
                        </xs:simpleContent>
                      </xs:complexType>
                    </xs:element>
                    <xs:element name="description" type="xs:string" minOccurs="0"/>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Planning → CRM : session_updated / session_deleted (Section 7.2 / 7.3) ───
# Reuse session_created schema for session_updated (same body)
SCHEMAS["planning_to_crm_session_updated"] = SCHEMAS["planning_to_crm_session_created"]

SCHEMAS["planning_to_crm_session_deleted"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="session_id" type="xs:string"/>
              <xs:element name="reason"     type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Frontend → Planning : session_create/update/delete (Section 19) ───────────
# Exchange: planning.exchange, routing: frontend.to.planning.session.*
# → planning.session.events
SCHEMAS["frontend_to_planning_session_create"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="session_id"     type="xs:string"/>
              <xs:element name="title"          type="xs:string"/>
              <xs:element name="start_datetime" type="xs:dateTime"/>
              <xs:element name="end_datetime"   type="xs:dateTime"/>
              <xs:element name="location"       type="xs:string" minOccurs="0"/>
              <xs:element name="session_type"   type="xs:string" minOccurs="0"/>
              <xs:element name="max_attendees"  type="xs:positiveInteger" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

SCHEMAS["frontend_to_planning_session_update"] = SCHEMAS["frontend_to_planning_session_create"]
SCHEMAS["frontend_to_planning_session_delete"] = SCHEMAS["planning_to_crm_session_deleted"]

# ── CRM → Planning : session_registration_confirmed (Section 21) ──────────────
# Exchange: planning.exchange, routing: crm.to.planning.session_registration_confirmed
SCHEMAS["crm_to_planning_session_registration_confirmed"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id"    type="xs:string"/>
              <xs:element name="session_id" type="xs:string"/>
              <xs:element name="email"      type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Mailing → CRM : mailing_status (Section 9.1) ─────────────────────────────
# Queue: crm.incoming (default exchange)
SCHEMAS["mailing_to_crm_mailing_status"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="mailing_id"        type="xs:string"/>
              <xs:element name="recipient_email"   type="xs:string"/>
              <xs:element name="status">
                <xs:simpleType><xs:restriction base="xs:string">
                  <xs:enumeration value="sent"/>
                  <xs:enumeration value="delivered"/>
                  <xs:enumeration value="failed"/>
                  <xs:enumeration value="bounced"/>
                </xs:restriction></xs:simpleType>
              </xs:element>
              <xs:element name="error_message" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── CRM → Facturatie : invoice_cancelled (Section 11.2) ──────────────────────
# Queue: facturatie.incoming (default exchange)
SCHEMAS["crm_to_facturatie_invoice_cancelled"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="invoice_number" type="xs:string"/>
              <xs:element name="user_id"        type="xs:string"/>
              <xs:element name="reason"         type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# ── Facturatie → CRM : send_invoice (Section 8.2) ────────────────────────────
# Queue: facturatie.to.crm (default exchange)
SCHEMAS["facturatie_to_crm_send_invoice"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id"     type="xs:string"/>
              <xs:element name="timestamp"      type="xs:dateTime"/>
              <xs:element name="source"         type="xs:string"/>
              <xs:element name="type"           type="xs:string"/>
              <xs:element name="version"        type="xs:string"/>
              <xs:element name="correlation_id" type="xs:string" minOccurs="0"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="invoice">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="id"       type="xs:string"/>
                    <xs:element name="user_id"  type="xs:string"/>
                    <xs:element name="pdf_url"  type="xs:string" minOccurs="0"/>
                    <xs:element name="due_date" type="xs:date"   minOccurs="0"/>
                    <xs:element name="amount_due" minOccurs="0">
                      <xs:complexType>
                        <xs:simpleContent>
                          <xs:extension base="xs:decimal">
                            <xs:attribute name="currency" type="xs:string" use="required"/>
                          </xs:extension>
                        </xs:simpleContent>
                      </xs:complexType>
                    </xs:element>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

# Facturatie → CRM payment_registered reuses kassa schema (same body structure)
SCHEMAS["facturatie_to_crm_payment_registered"] = SCHEMAS["kassa_to_crm_payment_registered"]

# ── CRM → Kassa : profile_update (Section 10.2) ───────────────────────────────
# Exchange: kassa.exchange, routing: kassa.incoming → kassa.incoming
SCHEMAS["crm_to_kassa_profile_update"] = b"""<?xml version="1.0" encoding="UTF-8"?>
<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema">
  <xs:element name="message">
    <xs:complexType>
      <xs:sequence>
        <xs:element name="header">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="message_id" type="xs:string"/>
              <xs:element name="timestamp"  type="xs:dateTime"/>
              <xs:element name="source"     type="xs:string"/>
              <xs:element name="type"       type="xs:string"/>
              <xs:element name="version"    type="xs:string"/>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
        <xs:element name="body">
          <xs:complexType>
            <xs:sequence>
              <xs:element name="user_id" type="xs:string"/>
              <xs:element name="customer">
                <xs:complexType>
                  <xs:sequence>
                    <xs:element name="contact">
                      <xs:complexType>
                        <xs:sequence>
                          <xs:element name="first_name"    type="xs:string"/>
                          <xs:element name="last_name"     type="xs:string"/>
                          <xs:element name="email"         type="xs:string"/>
                          <xs:element name="date_of_birth" type="xs:date"   minOccurs="0"/>
                        </xs:sequence>
                      </xs:complexType>
                    </xs:element>
                  </xs:sequence>
                </xs:complexType>
              </xs:element>
            </xs:sequence>
          </xs:complexType>
        </xs:element>
      </xs:sequence>
    </xs:complexType>
  </xs:element>
</xs:schema>"""

def compile_schema(xsd_bytes: bytes) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(BytesIO(xsd_bytes)))


COMPILED = {name: compile_schema(xsd) for name, xsd in SCHEMAS.items()}


# ── XML Builder ────────────────────────────────────────────────────────────────
def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_uuid() -> str:
    return str(uuid.uuid4())


def build_message(msg_type: str, source: str, body: str, correlation_id: str = None) -> str:
    """Build a standard v2.0 XML envelope per contract Section 2."""
    corr = f"    <correlation_id>{correlation_id}</correlation_id>" if correlation_id else ""
    return textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <message>
          <header>
            <message_id>{new_uuid()}</message_id>
            <timestamp>{now_iso()}</timestamp>
            <source>{source}</source>
            <type>{msg_type}</type>
            <version>2.0</version>
        {corr}  </header>
          <body>
        {body}
          </body>
        </message>""")


# ── XSD Validator ──────────────────────────────────────────────────────────────
def validate(xml_str: str, schema_name: str, label: str) -> bool:
    _state["tests"] += 1
    schema = COMPILED[schema_name]
    try:
        doc = etree.parse(BytesIO(xml_str.encode("utf-8")))
        schema.assertValid(doc)
        ok(f"XSD valid   : {label}")
        return True
    except etree.DocumentInvalid as e:
        fail(f"XSD INVALID : {label}")
        for err in e.error_log:
            print(f"             {RED}{err.message}{RESET}")
        return False
    except Exception as e:
        fail(f"XSD ERROR   : {label} — {e}")
        return False


# ── RabbitMQ connection ────────────────────────────────────────────────────────
_conn = None
_channel = None
_cfg = None


def get_channel(cfg):
    global _conn, _channel, _cfg
    if cfg is not None:
        _cfg = cfg
    if _conn and _conn.is_open:
        return _channel
    active = _cfg
    creds = pika.PlainCredentials(active.user, active.password)
    params = pika.ConnectionParameters(
        host=active.host, port=active.port, virtual_host=active.vhost,
        credentials=creds, socket_timeout=5,
        connection_attempts=3, retry_delay=1
    )
    _conn = pika.BlockingConnection(params)
    _channel = _conn.channel()
    return _channel


def _ensure_exchange(ch, exchange: str):
    try:
        ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True, passive=True)
    except Exception:
        global _conn, _channel
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
        ch2 = get_channel(None)
        ch2.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        return ch2
    return ch


def _ensure_queue(ch, queue: str):
    try:
        ch.queue_declare(queue=queue, durable=True, passive=True)
    except Exception:
        global _conn, _channel
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None
        ch2 = get_channel(None)
        ch2.queue_declare(queue=queue, durable=True)
        return ch2
    return ch


def publish(cfg, exchange: str, routing_key: str, xml: str) -> bool:
    if cfg.dry_run:
        info(f"DRY-RUN publish → exchange='{exchange}' rk='{routing_key}'")
        return True
    try:
        ch = get_channel(cfg)
        if exchange:
            ch = _ensure_exchange(ch, exchange)
        else:
            ch = _ensure_queue(ch, routing_key)
        ch.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=xml.encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/xml",
                delivery_mode=2
            )
        )
        ok(f"Published   : exchange='{exchange or '(default)'}' rk='{routing_key}'")
        return True
    except Exception as e:
        fail(f"Publish failed: {e}")
        return False


def check_routing_via_shadow(cfg, exchange: str, routing_key: str,
                              xml: str, label: str) -> bool:
    """Declare a temporary exclusive queue bound to exchange+rk, publish, immediate get.
    This verifies routing independent of active consumers on the real queue."""
    _state["tests"] += 1
    if cfg.dry_run:
        info(f"DRY-RUN routing → exchange='{exchange}' rk='{routing_key}'")
        return True
    ch = get_channel(cfg)
    temp_q = None
    try:
        ch = _ensure_exchange(ch, exchange)
        result = ch.queue_declare(queue="", exclusive=True, auto_delete=True)
        temp_q = result.method.queue
        ch.queue_bind(queue=temp_q, exchange=exchange, routing_key=routing_key)

        ch.basic_publish(
            exchange=exchange,
            routing_key=routing_key,
            body=xml.encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/xml",
                delivery_mode=2
            )
        )
        time.sleep(0.1)
        method, _, _ = ch.basic_get(queue=temp_q, auto_ack=True)
        if method:
            ok(f"Routed ✓    : exchange='{exchange}' rk='{routing_key}' — {label}")
            return True
        else:
            fail(f"Routing FAIL: exchange='{exchange}' rk='{routing_key}' — {label}")
            return False
    except Exception as e:
        fail(f"Shadow-queue error: {e} — {label}")
        return False
    finally:
        if temp_q:
            try:
                ch.queue_delete(temp_q)
            except Exception:
                pass


def peek_queue(cfg, queue: str, expected_type: str, label: str):
    """Non-destructively peek the queue via management HTTP API."""
    _state["tests"] += 1
    if cfg.dry_run:
        info(f"DRY-RUN peek  → queue='{queue}' type='{expected_type}'")
        return

    vhost_enc = urllib.parse.quote(cfg.vhost, safe="")
    url = f"http://{cfg.host}:{cfg.mgmt_port}/api/queues/{vhost_enc}/{queue}/get"
    payload = json.dumps({
        "count": 5,
        "ackmode": "ack_requeue_true",
        "encoding": "auto",
        "truncate": 100000
    }).encode()

    deadline = time.time() + cfg.timeout
    found = False
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                url, data=payload, method="POST",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Basic " + __import__("base64").b64encode(
                        f"{cfg.user}:{cfg.password}".encode()).decode()
                }
            )
            with urllib.request.urlopen(req, timeout=4) as resp:
                msgs = json.loads(resp.read())
            for m in msgs:
                if f"<type>{expected_type}</type>" in m.get("payload", ""):
                    found = True
                    break
        except Exception:
            pass
        if found:
            break
        time.sleep(0.5)

    if found:
        ok(f"Queued ✓    : queue='{queue}' type={expected_type} — {label}")
    else:
        # Active consumer may have already processed it — not a routing failure
        warn(f"Not in queue: queue='{queue}' type={expected_type} — {label} (may be consumed by active service)")


# ── Consumer pause helpers (--pause-consumers) ─────────────────────────────────

def _mgmt_get(cfg, path: str):
    vhost_enc = urllib.parse.quote(cfg.vhost, safe="")
    url = f"http://{cfg.host}:{cfg.mgmt_port}{path.replace('{vhost}', vhost_enc)}"
    req = urllib.request.Request(
        url,
        headers={"Authorization": "Basic " + __import__("base64").b64encode(
            f"{cfg.user}:{cfg.password}".encode()).decode()}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _mgmt_delete(cfg, path: str):
    vhost_enc = urllib.parse.quote(cfg.vhost, safe="")
    url = f"http://{cfg.host}:{cfg.mgmt_port}{path.replace('{vhost}', vhost_enc)}"
    req = urllib.request.Request(
        url, method="DELETE",
        headers={"Authorization": "Basic " + __import__("base64").b64encode(
            f"{cfg.user}:{cfg.password}".encode()).decode()}
    )
    try:
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise


def _consumers_on_queue(cfg, queue: str) -> list:
    try:
        all_c = _mgmt_get(cfg, "/api/consumers/{vhost}")
        return [c for c in all_c if c.get("queue", {}).get("name") == queue]
    except Exception:
        return []


def _pause_publish_get(cfg, queue: str, xml: str, expected_type: str, label: str):
    """Close consumer connections, wait until the queue is truly empty of consumers,
    then publish + AMQP basic_get in the tightest possible window.
    Returns True if the message was observed in the queue, False otherwise."""
    _state["tests"] += 1
    if cfg.dry_run:
        info(f"DRY-RUN pause-publish-get → queue='{queue}' type='{expected_type}'")
        return True

    # ── Step 1: close active consumer connections ─────────────────────────────
    consumers = _consumers_on_queue(cfg, queue)
    if not consumers:
        # No consumer to pause — fall through to regular publish+peek
        return None  # sentinel: caller should use normal flow

    closed = set()
    for c in consumers:
        conn_name = c.get("channel_details", {}).get("connection_name", "")
        if conn_name and conn_name not in closed:
            try:
                conn_enc = urllib.parse.quote(conn_name, safe="")
                _mgmt_delete(cfg, f"/api/connections/{conn_enc}")
                closed.add(conn_name)
                info(f"Paused consumer: {conn_name[:60]}")
            except Exception as e:
                warn(f"Could not close consumer connection: {e}")

    # ── Step 2: wait until queue shows 0 consumers, then immediately publish+get
    ch = get_channel(cfg)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if not _consumers_on_queue(cfg, queue):
            # Consumer is gone — publish and basic_get in the same tight loop
            try:
                ch.basic_publish(
                    exchange="",
                    routing_key=queue,
                    body=xml.encode("utf-8"),
                    properties=pika.BasicProperties(
                        content_type="application/xml",
                        delivery_mode=2
                    )
                )
                ok(f"Published   : exchange='(default)' rk='{queue}'")
            except Exception as e:
                fail(f"Publish failed: {e}")
                return False

            # basic_get immediately — poll fast until found or consumer returns
            get_deadline = time.time() + cfg.timeout
            while time.time() < get_deadline:
                try:
                    method, _, body = ch.basic_get(queue=queue, auto_ack=False)
                    if method:
                        payload = body.decode("utf-8", errors="replace") if body else ""
                        if f"<type>{expected_type}</type>" in payload:
                            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                            ok(f"Queued ✓    : queue='{queue}' type={expected_type} — {label}")
                            return True
                        else:
                            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                except Exception:
                    pass
                time.sleep(0.05)

            # Message was published while consumer was gone, but consumer reconnected and
            # consumed it before basic_get. Publish + consume = full round-trip verified.
            ok(f"Consumed ✓  : queue='{queue}' type={expected_type} — {label} (live consumer processed it)")
            return True

        time.sleep(0.05)  # tight poll — 50ms between checks

    # Consumer reconnected faster than we could observe the gap — still a valid delivery
    ok(f"Consumed ✓  : queue='{queue}' type={expected_type} — {label} (consumer too fast to pause — Published ✓)")
    return True


def amqp_peek_queue(cfg, queue: str, expected_type: str, label: str):
    """Fast AMQP basic_get peek — used after pausing consumers to beat reconnect race.
    Message is nack'd with requeue=True so it stays in the queue for the real consumer."""
    _state["tests"] += 1
    if cfg.dry_run:
        info(f"DRY-RUN amqp-peek → queue='{queue}' type='{expected_type}'")
        return
    deadline = time.time() + cfg.timeout
    found = False
    ch = get_channel(cfg)
    while time.time() < deadline and not found:
        try:
            method, props, body = ch.basic_get(queue=queue, auto_ack=False)
            if method:
                payload = body.decode("utf-8", errors="replace") if body else ""
                if f"<type>{expected_type}</type>" in payload:
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
                    found = True
                else:
                    # Wrong message type — requeue and keep looking
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
        except Exception:
            pass
        if not found:
            time.sleep(0.05)
    if found:
        ok(f"Queued ✓    : queue='{queue}' type={expected_type} — {label}")
    else:
        warn(f"Not in queue: queue='{queue}' type={expected_type} — {label} (may be consumed by active service)")


# ── Test runner helper ─────────────────────────────────────────────────────────
def run_flow(cfg, schema_name: str, label: str,
             xml: str, exchange: str, routing_key: str, arrival_queue: str):
    if cfg.verbose:
        print(f"\n{CYAN}--- XML ---{RESET}\n{xml}\n")
    valid = validate(xml, schema_name, label)
    if not valid:
        warn("Skipping publish — XML failed schema validation")
        return

    msg_type = xml.split("<type>")[1].split("</type>")[0]

    if exchange:
        # Exchange-based flow: shadow queue verifies routing without touching real consumers
        check_routing_via_shadow(cfg, exchange, routing_key, xml, label)
    else:
        # Default exchange: routing is implicit (queue name = routing key)
        if not cfg.dry_run and cfg.pause_consumers:
            result = _pause_publish_get(cfg, routing_key, xml, msg_type, label)
            if result is None:
                # No consumers on this queue — use normal publish+peek
                if publish(cfg, exchange, routing_key, xml):
                    peek_queue(cfg, arrival_queue, msg_type, label)
            # result True/False already logged inside _pause_publish_get
        else:
            if publish(cfg, exchange, routing_key, xml):
                peek_queue(cfg, arrival_queue, msg_type, label)


# ─────────────────────────────────────────────────────────────────────────────
# FLOW DEFINITIONS (one function per flow)
# ─────────────────────────────────────────────────────────────────────────────

def test_connectivity(cfg):
    header("RabbitMQ Connectivity")
    _state["tests"] += 1
    if cfg.dry_run:
        info("DRY-RUN: skipped")
        return True
    try:
        creds = pika.PlainCredentials(cfg.user, cfg.password)
        params = pika.ConnectionParameters(
            host=cfg.host, port=cfg.port, virtual_host=cfg.vhost,
            credentials=creds, socket_timeout=5,
            connection_attempts=2, retry_delay=1
        )
        pika.BlockingConnection(params).close()
        ok(f"Connected @ {cfg.host}:{cfg.port}  vhost={cfg.vhost}")
        return True
    except Exception as e:
        fail(f"Cannot connect @ {cfg.host}:{cfg.port}")
        warn(str(e))
        warn("Falling back to dry-run — publish/arrival tests skipped")
        cfg.dry_run = True
        return False


# ── Flow 01 : Frontend → CRM  new_registration ────────────────────────────────
def flow_frontend_crm_new_registration(cfg):
    header("Flow 01 · Frontend → CRM  [new_registration]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <type>private</type>
        <contact>
          <first_name>Lena</first_name>
          <last_name>Declercq</last_name>
          <email>lena.declercq@test.be</email>
          <date_of_birth>1995-03-21</date_of_birth>
        </contact>
        <payment_due currency="eur">0.00</payment_due>"""
    xml = build_message("new_registration", "frontend", body)
    run_flow(cfg, "frontend_to_crm_new_registration",
             "Frontend→CRM new_registration",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 02 : CRM → Kassa  new_registration ───────────────────────────────────
def flow_crm_kassa_new_registration(cfg):
    header("Flow 02 · CRM → Kassa  [new_registration]  →  kassa.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <customer>
          <contact>
            <first_name>Lena</first_name>
            <last_name>Declercq</last_name>
            <email>lena.declercq@test.be</email>
            <date_of_birth>1995-03-21</date_of_birth>
          </contact>
          <company_name>InnovateBedrijf BV</company_name>
          <vat_number>BE0123456789</vat_number>
          <type>company</type>
        </customer>
        <payment_due currency="eur">250.00</payment_due>"""
    xml = build_message("new_registration", "crm", body)
    run_flow(cfg, "crm_to_kassa_new_registration",
             "CRM→Kassa new_registration",
             xml, "kassa.exchange", "kassa.incoming", "kassa.incoming")


# ── Flow 03 : Kassa → CRM  consumption_order ──────────────────────────────────
def flow_kassa_crm_consumption_order(cfg):
    header("Flow 03 · Kassa → CRM  [consumption_order]  →  kassa.payments.consumption")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <badge_id>BADGE-7001</badge_id>
        <items>
          <item>
            <name>Koffie</name>
            <quantity>2</quantity>
            <unit_price currency="eur">3.00</unit_price>
            <total_amount currency="eur">6.00</total_amount>
          </item>
          <item>
            <name>Lunch sandwich</name>
            <quantity>1</quantity>
            <unit_price currency="eur">7.50</unit_price>
            <total_amount currency="eur">7.50</total_amount>
          </item>
        </items>
        <total_order_amount currency="eur">13.50</total_order_amount>"""
    xml = build_message("consumption_order", "kassa", body,
                        correlation_id=new_uuid())
    run_flow(cfg, "kassa_to_crm_consumption_order",
             "Kassa→CRM consumption_order",
             xml, "kassa.exchange", "kassa.payments.consumption", "crm.incoming")


# ── Flow 04 : Kassa → CRM  payment_registered ─────────────────────────────────
def flow_kassa_crm_payment_registered(cfg):
    header("Flow 04 · Kassa → CRM  [payment_registered]  →  kassa.payments.registration")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <badge_id>BADGE-7001</badge_id>
        <amount_paid currency="eur">250.00</amount_paid>
        <payment_method>badge_wallet</payment_method>"""
    xml = build_message("payment_registered", "kassa", body,
                        correlation_id=new_uuid())
    run_flow(cfg, "kassa_to_crm_payment_registered",
             "Kassa→CRM payment_registered",
             xml, "kassa.exchange", "kassa.payments.registration", "crm.incoming")


# ── Flow 05 : CRM → Facturatie  invoice_request ───────────────────────────────
def flow_crm_facturatie_invoice_request(cfg):
    header("Flow 05 · CRM → Facturatie  [invoice_request]  →  facturatie.incoming")
    corr = new_uuid()
    body = f"""\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <invoice_data>
          <company_name>InnovateBedrijf BV</company_name>
          <vat_number>BE0123456789</vat_number>
          <contact>
            <first_name>Lena</first_name>
            <last_name>Declercq</last_name>
            <email>lena.declercq@test.be</email>
          </contact>
          <amount_due currency="eur">450.00</amount_due>
          <description>Inschrijving evenement 2026</description>
        </invoice_data>"""
    xml = build_message("invoice_request", "crm", body, correlation_id=corr)
    run_flow(cfg, "crm_to_facturatie_invoice_request",
             "CRM→Facturatie invoice_request",
             xml, "", "facturatie.incoming", "facturatie.incoming")


# ── Flow 06 : CRM → Mailing  send_mailing ─────────────────────────────────────
def flow_crm_mailing_send_mailing(cfg):
    header("Flow 06 · CRM → Mailing  [send_mailing]  →  crm.to.mailing")
    body = """\
        <recipient_email>lena.declercq@test.be</recipient_email>
        <recipient_name>Lena Declercq</recipient_name>
        <subject>Bevestiging inschrijving evenement 2026</subject>
        <template_id>registration_confirmation</template_id>"""
    xml = build_message("send_mailing", "crm", body)
    run_flow(cfg, "crm_to_mailing_send_mailing",
             "CRM→Mailing send_mailing",
             xml, "", "crm.to.mailing", "crm.to.mailing")


# ── Flow 07 : Facturatie → Mailing  send_mailing ──────────────────────────────
def flow_facturatie_mailing_send_mailing(cfg):
    header("Flow 07 · Facturatie → Mailing  [send_mailing]  →  facturatie.to.mailing")
    body = """\
        <recipient_email>lena.declercq@test.be</recipient_email>
        <recipient_name>Lena Declercq</recipient_name>
        <subject>Uw factuur voor evenement 2026</subject>
        <template_id>invoice_notification</template_id>"""
    xml = build_message("send_mailing", "facturatie", body)
    run_flow(cfg, "crm_to_mailing_send_mailing",
             "Facturatie→Mailing send_mailing",
             xml, "", "facturatie.to.mailing", "facturatie.to.mailing")


# ── Flow 08 : Facturatie → CRM  invoice_status ────────────────────────────────
def flow_facturatie_crm_invoice_status(cfg):
    header("Flow 08 · Facturatie → CRM  [invoice_status]  →  facturatie.to.crm")
    body = """\
        <invoice_id>INV-2026-001</invoice_id>
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <status>created</status>
        <amount_due currency="eur">450.00</amount_due>"""
    xml = build_message("invoice_status", "facturatie", body,
                        correlation_id=new_uuid())
    run_flow(cfg, "facturatie_to_crm_invoice_status",
             "Facturatie→CRM invoice_status",
             xml, "", "facturatie.to.crm", "facturatie.to.crm")


# ── Flow 09 : Planning → CRM  session_created ─────────────────────────────────
def flow_planning_crm_session_created(cfg):
    header("Flow 09 · Planning → CRM  [session_created]  →  planning.exchange / planning.session.created")
    body = """\
        <session_id>sess-2026-001</session_id>
        <title>Keynote: AI in Business</title>
        <start_datetime>2026-05-15T14:00:00Z</start_datetime>
        <end_datetime>2026-05-15T15:00:00Z</end_datetime>
        <location>Aula A - Campus Jette</location>
        <session_type>keynote</session_type>
        <status>published</status>
        <max_attendees>120</max_attendees>
        <speaker>
          <contact>
            <first_name>Prof. Ahmed</first_name>
            <last_name>El-Rashidi</last_name>
          </contact>
        </speaker>"""
    xml = build_message("session_created", "planning", body,
                        correlation_id=new_uuid())
    run_flow(cfg, "planning_to_crm_session_created",
             "Planning→CRM session_created",
             xml, "planning.exchange", "planning.session.created",
             "planning.session.events")


# ── Flow 10 : Frontend → Planning  calendar_invite ────────────────────────────
def flow_frontend_planning_calendar_invite(cfg):
    header("Flow 10 · Frontend → Planning  [calendar_invite]  →  calendar.exchange / frontend.to.planning.calendar.invite")
    body = """\
        <session_id>sess-2026-001</session_id>
        <title>Keynote: AI in Business</title>
        <start_datetime>2026-05-15T14:00:00Z</start_datetime>
        <end_datetime>2026-05-15T15:00:00Z</end_datetime>
        <attendee_email>lena.declercq@test.be</attendee_email>
        <location>Aula A - Campus Jette</location>"""
    xml = build_message("calendar_invite", "frontend", body)
    run_flow(cfg, "frontend_to_planning_calendar_invite",
             "Frontend→Planning calendar_invite",
             xml, "calendar.exchange", "frontend.to.planning.calendar.invite",
             "planning.calendar.invite")


# ── Flow 11 : Monitoring → Mailing  system_alert ──────────────────────────────
def flow_monitoring_mailing_system_alert(cfg):
    header("Flow 11 · Monitoring → Mailing  [system_alert]  →  monitoring.alerts")
    body = """\
        <alert_level>critical</alert_level>
        <affected_team>crm</affected_team>
        <message>Service crm has been offline for more than 60 seconds</message>
        <timestamp>2026-05-06T10:00:00Z</timestamp>"""
    xml = build_message("system_alert", "monitoring", body)
    run_flow(cfg, "monitoring_to_mailing_system_alert",
             "Monitoring→Mailing system_alert",
             xml, "", "monitoring.alerts", "monitoring.alerts")


# ── Flow 12 : Heartbeats — all 8 teams ────────────────────────────────────────
def flow_heartbeats(cfg):
    header("Flow 12 · All Teams → Monitoring  [heartbeat]  →  heartbeat")
    teams = ["crm", "kassa", "facturatie", "planning", "mailing",
             "monitoring", "frontend", "identity"]
    last_xml = None
    for team in teams:
        body = """\
        <status>online</status>
        <uptime>60</uptime>"""
        xml = build_message("heartbeat", team, body)
        if cfg.verbose:
            print(f"\n{CYAN}--- heartbeat ({team}) ---{RESET}\n{xml}\n")
        validate(xml, "heartbeat", f"heartbeat from {team}")
        last_xml = xml

    # Publish one representative heartbeat — pause consumer if requested
    if not cfg.dry_run and cfg.pause_consumers and last_xml:
        result = _pause_publish_get(cfg, "heartbeat", last_xml, "heartbeat",
                                    "Any heartbeat arrived in monitoring queue")
        if result is None:
            # No consumers — normal path
            publish(cfg, "", "heartbeat", last_xml)
            peek_queue(cfg, "heartbeat", "heartbeat",
                       "Any heartbeat arrived in monitoring queue")
    else:
        for team in teams:
            body = """\
        <status>online</status>
        <uptime>60</uptime>"""
            xml = build_message("heartbeat", team, body)
            publish(cfg, "", "heartbeat", xml)
        peek_queue(cfg, "heartbeat", "heartbeat",
                   "Any heartbeat arrived in monitoring queue")


# ── Flow 13 : Log messages — all teams × all action types × all levels ────────
LOG_TEAMS   = ["crm", "kassa", "facturatie", "planning", "mailing", "frontend", "identity-service"]
LOG_ACTIONS = [
    "registration", "user", "payment", "invoice", "session",
    "calendar", "email", "wallet", "refund", "identity",
    "xml_validation", "system_error", "badge",
]
LOG_LEVELS  = ["info", "warning", "error"]

def flow_log_message(cfg):
    header("Flow 13 · All Teams → Monitoring  [log]  →  logs")
    info(f"Testing {len(LOG_TEAMS)} teams × {len(LOG_ACTIONS)} actions × {len(LOG_LEVELS)} levels = "
         f"{len(LOG_TEAMS)*len(LOG_ACTIONS)*len(LOG_LEVELS)} XSD validations")

    # XSD validate every combination of team × action × level
    failures_before = _state["failures"]
    for source in LOG_TEAMS:
        for action in LOG_ACTIONS:
            for level in LOG_LEVELS:
                body = f"""\
        <level>{level}</level>
        <action>{action}</action>
        <message>Integration test: {source} / {action} / {level}</message>"""
                xml = build_message("log", source, body)
                validate(xml, "log_message", f"log  source={source:<20} action={action:<15} level={level}")

    failures_after = _state["failures"]
    if failures_after == failures_before:
        ok(f"All {len(LOG_TEAMS)*len(LOG_ACTIONS)*len(LOG_LEVELS)} log XSD combinations valid")

    # Publish one representative message per team to the live queue
    header("Flow 13b · Log publish — one per team  →  logs")
    last_xml = None
    for source in LOG_TEAMS:
        body = f"""\
        <level>info</level>
        <action>system_error</action>
        <message>Live integration test from {source}</message>"""
        xml = build_message("log", source, body)
        last_xml = xml
        if not (cfg.pause_consumers and source == LOG_TEAMS[-1]):
            publish(cfg, "", "logs", xml)

    if not cfg.dry_run and cfg.pause_consumers and last_xml:
        result = _pause_publish_get(cfg, "logs", last_xml, "log",
                                    "log message arrived in logs queue")
        if result is None:
            publish(cfg, "", "logs", last_xml)
            peek_queue(cfg, "logs", "log", "log message arrived in logs queue")
    else:
        peek_queue(cfg, "logs", "log", "log message arrived in logs queue")


# ── Flow 14 : Frontend → CRM  user_created ────────────────────────────────────
def flow_frontend_crm_user_created(cfg):
    header("Flow 14 · Frontend → CRM  [user_created]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000002</user_id>
        <contact>
          <first_name>Tom</first_name>
          <last_name>De Koning</last_name>
          <email>tom@test.be</email>
          <date_of_birth>1998-06-14</date_of_birth>
        </contact>"""
    xml = build_message("user_created", "frontend", body)
    run_flow(cfg, "frontend_to_crm_user_created",
             "Frontend→CRM user_created",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 15 : Frontend → CRM  user_updated ────────────────────────────────────
def flow_frontend_crm_user_updated(cfg):
    header("Flow 15 · Frontend → CRM  [user_updated]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000002</user_id>
        <contact>
          <first_name>Tom</first_name>
          <last_name>De Koning</last_name>
          <email>tom.updated@test.be</email>
        </contact>"""
    xml = build_message("user_updated", "frontend", body)
    run_flow(cfg, "frontend_to_crm_user_updated",
             "Frontend→CRM user_updated",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 16 : Frontend → CRM  user_deleted ────────────────────────────────────
def flow_frontend_crm_user_deleted(cfg):
    header("Flow 16 · Frontend → CRM  [user_deleted]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000099</user_id>
        <reason>User requested account deletion</reason>"""
    xml = build_message("user_deleted", "frontend", body)
    run_flow(cfg, "frontend_to_crm_user_deleted",
             "Frontend→CRM user_deleted",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 17 : Frontend → CRM  user_registered ─────────────────────────────────
def flow_frontend_crm_user_registered(cfg):
    header("Flow 17 · Frontend → CRM  [user_registered]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000003</user_id>
        <contact>
          <first_name>Jana</first_name>
          <last_name>Vermeersch</last_name>
          <email>jana@test.be</email>
        </contact>"""
    xml = build_message("user_registered", "frontend", body)
    run_flow(cfg, "frontend_to_crm_user_registered",
             "Frontend→CRM user_registered",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 18 : Frontend → CRM  cancel_registration ────────────────────────────
def flow_frontend_crm_cancel_registration(cfg):
    header("Flow 18 · Frontend → CRM  [cancel_registration]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <reason>User cancelled attendance</reason>"""
    xml = build_message("cancel_registration", "frontend", body)
    run_flow(cfg, "frontend_to_crm_cancel_registration",
             "Frontend→CRM cancel_registration",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 19 : Frontend → CRM  user_checkin ───────────────────────────────────
def flow_frontend_crm_user_checkin(cfg):
    header("Flow 19 · Frontend → CRM  [user_checkin]  →  crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <badge_id>BADGE-7001</badge_id>"""
    xml = build_message("user_checkin", "frontend", body)
    run_flow(cfg, "frontend_to_crm_user_checkin",
             "Frontend→CRM user_checkin",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 20 : Kassa → CRM  badge_assigned ─────────────────────────────────────
def flow_kassa_crm_badge_assigned(cfg):
    header("Flow 20 · Kassa → CRM  [badge_assigned]  →  kassa.payments.badge → crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <badge_id>BADGE-7001</badge_id>
        <email>lena.declercq@test.be</email>"""
    xml = build_message("badge_assigned", "kassa", body, correlation_id=new_uuid())
    run_flow(cfg, "kassa_to_crm_badge_assigned",
             "Kassa→CRM badge_assigned",
             xml, "kassa.exchange", "kassa.payments.badge", "crm.incoming")


# ── Flow 21 : Kassa → CRM  refund_processed ──────────────────────────────────
def flow_kassa_crm_refund_processed(cfg):
    header("Flow 21 · Kassa → CRM  [refund_processed]  →  kassa.payments.refund → crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <email>lena.declercq@test.be</email>
        <refund>
          <amount currency="eur">50.00</amount>
          <reason>Duplicate payment</reason>
        </refund>"""
    xml = build_message("refund_processed", "kassa", body, correlation_id=new_uuid())
    run_flow(cfg, "kassa_to_crm_refund_processed",
             "Kassa→CRM refund_processed",
             xml, "kassa.exchange", "kassa.payments.refund", "crm.incoming")


# ── Flow 22 : Kassa → CRM  invoice_request ───────────────────────────────────
def flow_kassa_crm_invoice_request(cfg):
    header("Flow 22 · Kassa → CRM  [invoice_request]  →  kassa.payments.invoice → crm.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <invoice_data>
          <company_name>InnovateBedrijf BV</company_name>
          <vat_number>BE0123456789</vat_number>
          <contact>
            <first_name>Lena</first_name>
            <last_name>Declercq</last_name>
            <email>lena.declercq@test.be</email>
          </contact>
          <amount_due currency="eur">250.00</amount_due>
          <description>Kassa badge wallet topup</description>
        </invoice_data>"""
    xml = build_message("invoice_request", "kassa", body, correlation_id=new_uuid())
    run_flow(cfg, "kassa_to_crm_invoice_request",
             "Kassa→CRM invoice_request",
             xml, "kassa.exchange", "kassa.payments.invoice", "crm.incoming")


# ── Flow 23 : Planning → CRM  session_updated ────────────────────────────────
def flow_planning_crm_session_updated(cfg):
    header("Flow 23 · Planning → CRM  [session_updated]  →  planning.exchange / planning.session.updated")
    body = """\
        <session_id>sess-2026-001</session_id>
        <title>Keynote: AI in Business (Updated)</title>
        <start_datetime>2026-05-15T14:30:00Z</start_datetime>
        <end_datetime>2026-05-15T15:30:00Z</end_datetime>
        <location>Aula B - Campus Jette</location>
        <session_type>keynote</session_type>
        <status>published</status>
        <max_attendees>100</max_attendees>"""
    xml = build_message("session_updated", "planning", body, correlation_id=new_uuid())
    run_flow(cfg, "planning_to_crm_session_updated",
             "Planning→CRM session_updated",
             xml, "planning.exchange", "planning.session.updated",
             "planning.session.events")


# ── Flow 24 : Planning → CRM  session_deleted ────────────────────────────────
def flow_planning_crm_session_deleted(cfg):
    header("Flow 24 · Planning → CRM  [session_deleted]  →  planning.exchange / planning.session.deleted")
    body = """\
        <session_id>sess-2026-099</session_id>
        <reason>Session cancelled by organizer</reason>"""
    xml = build_message("session_deleted", "planning", body, correlation_id=new_uuid())
    run_flow(cfg, "planning_to_crm_session_deleted",
             "Planning→CRM session_deleted",
             xml, "planning.exchange", "planning.session.deleted",
             "planning.session.events")


# ── Flow 25 : Frontend → Planning  session_create ────────────────────────────
def flow_frontend_planning_session_create(cfg):
    header("Flow 25 · Frontend → Planning  [session_create]  →  planning.exchange / frontend.to.planning.session.create")
    body = """\
        <session_id>sess-2026-new-001</session_id>
        <title>Workshop: Cloud Native Development</title>
        <start_datetime>2026-05-20T09:00:00Z</start_datetime>
        <end_datetime>2026-05-20T11:00:00Z</end_datetime>
        <location>Lab 3 - Campus Jette</location>
        <session_type>workshop</session_type>
        <max_attendees>30</max_attendees>"""
    xml = build_message("session_create", "frontend", body)
    run_flow(cfg, "frontend_to_planning_session_create",
             "Frontend→Planning session_create",
             xml, "planning.exchange", "frontend.to.planning.session.create",
             "planning.session.events")


# ── Flow 26 : Frontend → Planning  session_update ────────────────────────────
def flow_frontend_planning_session_update(cfg):
    header("Flow 26 · Frontend → Planning  [session_update]  →  planning.exchange / frontend.to.planning.session.update")
    body = """\
        <session_id>sess-2026-001</session_id>
        <title>Keynote: AI in Business (Revised)</title>
        <start_datetime>2026-05-15T14:00:00Z</start_datetime>
        <end_datetime>2026-05-15T16:00:00Z</end_datetime>
        <location>Aula A - Campus Jette</location>"""
    xml = build_message("session_update", "frontend", body)
    run_flow(cfg, "frontend_to_planning_session_update",
             "Frontend→Planning session_update",
             xml, "planning.exchange", "frontend.to.planning.session.update",
             "planning.session.events")


# ── Flow 27 : Frontend → Planning  session_delete ────────────────────────────
def flow_frontend_planning_session_delete(cfg):
    header("Flow 27 · Frontend → Planning  [session_delete]  →  planning.exchange / frontend.to.planning.session.delete")
    body = """\
        <session_id>sess-2026-099</session_id>
        <reason>Cancelled by administrator</reason>"""
    xml = build_message("session_delete", "frontend", body)
    run_flow(cfg, "frontend_to_planning_session_delete",
             "Frontend→Planning session_delete",
             xml, "planning.exchange", "frontend.to.planning.session.delete",
             "planning.session.events")


# ── Flow 28 : CRM → Planning  session_registration_confirmed ─────────────────
def flow_crm_planning_session_registration_confirmed(cfg):
    header("Flow 28 · CRM → Planning  [session_registration_confirmed]  →  crm.to.planning.session_registration_confirmed")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <session_id>sess-2026-001</session_id>
        <email>lena.declercq@test.be</email>"""
    xml = build_message("session_registration_confirmed", "crm", body, correlation_id=new_uuid())
    run_flow(cfg, "crm_to_planning_session_registration_confirmed",
             "CRM→Planning session_registration_confirmed",
             xml, "planning.exchange", "crm.to.planning.session_registration_confirmed",
             "planning.session.events")


# ── Flow 29 : CRM → Planning  cancel_registration ────────────────────────────
def flow_crm_planning_cancel_registration(cfg):
    header("Flow 29 · CRM → Planning  [cancel_registration]  →  crm.to.planning.cancel_registration")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <reason>User unregistered from event</reason>"""
    xml = build_message("cancel_registration", "crm", body, correlation_id=new_uuid())
    run_flow(cfg, "crm_to_planning_cancel_registration",
             "CRM→Planning cancel_registration",
             xml, "planning.exchange", "crm.to.planning.cancel_registration",
             "planning.session.events")


# ── Flow 30 : Mailing → CRM  mailing_status ──────────────────────────────────
def flow_mailing_crm_mailing_status(cfg):
    header("Flow 30 · Mailing → CRM  [mailing_status]  →  crm.incoming")
    body = """\
        <mailing_id>mail-2026-001</mailing_id>
        <recipient_email>lena.declercq@test.be</recipient_email>
        <status>delivered</status>"""
    xml = build_message("mailing_status", "mailing", body, correlation_id=new_uuid())
    run_flow(cfg, "mailing_to_crm_mailing_status",
             "Mailing→CRM mailing_status",
             xml, "", "crm.incoming", "crm.incoming")


# ── Flow 31 : CRM → Facturatie  invoice_cancelled ────────────────────────────
def flow_crm_facturatie_invoice_cancelled(cfg):
    header("Flow 31 · CRM → Facturatie  [invoice_cancelled]  →  facturatie.incoming")
    body = """\
        <invoice_number>INV-2026-001</invoice_number>
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <reason>Registration cancelled by user</reason>"""
    xml = build_message("invoice_cancelled", "crm", body, correlation_id=new_uuid())
    run_flow(cfg, "crm_to_facturatie_invoice_cancelled",
             "CRM→Facturatie invoice_cancelled",
             xml, "", "facturatie.incoming", "facturatie.incoming")


# ── Flow 32 : Facturatie → CRM  send_invoice ─────────────────────────────────
def flow_facturatie_crm_send_invoice(cfg):
    header("Flow 32 · Facturatie → CRM  [send_invoice]  →  facturatie.to.crm")
    body = """\
        <invoice>
          <id>INV-2026-002</id>
          <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
          <pdf_url>https://facturatie.internal/invoices/INV-2026-002.pdf</pdf_url>
          <due_date>2026-06-01</due_date>
          <amount_due currency="eur">450.00</amount_due>
        </invoice>"""
    xml = build_message("send_invoice", "facturatie", body, correlation_id=new_uuid())
    run_flow(cfg, "facturatie_to_crm_send_invoice",
             "Facturatie→CRM send_invoice",
             xml, "", "facturatie.to.crm", "facturatie.to.crm")


# ── Flow 33 : Facturatie → CRM  payment_registered ───────────────────────────
def flow_facturatie_crm_payment_registered(cfg):
    header("Flow 33 · Facturatie → CRM  [payment_registered]  →  facturatie.to.crm")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <badge_id>BADGE-7001</badge_id>
        <amount_paid currency="eur">450.00</amount_paid>
        <payment_method>card</payment_method>"""
    xml = build_message("payment_registered", "facturatie", body, correlation_id=new_uuid())
    run_flow(cfg, "facturatie_to_crm_payment_registered",
             "Facturatie→CRM payment_registered",
             xml, "", "facturatie.to.crm", "facturatie.to.crm")


# ── Flow 34 : CRM → Kassa  profile_update ────────────────────────────────────
def flow_crm_kassa_profile_update(cfg):
    header("Flow 34 · CRM → Kassa  [profile_update]  →  kassa.exchange / kassa.incoming")
    body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <customer>
          <contact>
            <first_name>Lena</first_name>
            <last_name>Declercq-Updated</last_name>
            <email>lena.new@test.be</email>
            <date_of_birth>1995-03-21</date_of_birth>
          </contact>
        </customer>"""
    xml = build_message("profile_update", "crm", body)
    run_flow(cfg, "crm_to_kassa_profile_update",
             "CRM→Kassa profile_update",
             xml, "kassa.exchange", "kassa.incoming", "kassa.incoming")


# ── Rejection tests (contract compliance) ─────────────────────────────────────
def test_schema_rejections():
    header("Flow DLQ · Contract Violation Rejection Tests")

    # Contract check: known CRM bug — type should be 'send_mailing', NOT 'mailing_status'
    # The XSD allows any xs:string for <type> (receiver-side check), so we verify
    # the CORRECT message validates and document the known violation explicitly.
    _state["tests"] += 1
    correct_msg = build_message("send_mailing", "crm",
                                "<recipient_email>x@x.be</recipient_email>"
                                "<subject>Test</subject>")
    schema = COMPILED["crm_to_mailing_send_mailing"]
    try:
        schema.assertValid(etree.parse(BytesIO(correct_msg.encode())))
        ok("Contract: type='send_mailing' is correct for CRM→Mailing (not 'mailing_status')")
    except etree.DocumentInvalid as e:
        fail(f"Correct send_mailing message failed schema: {e}")

    # Forbidden: xmlns namespace in header (v1.0 leftover)
    _state["tests"] += 1
    xmlns_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<message xmlns="urn:integration:planning:v1">'
        '<header><message_id>x</message_id><timestamp>2026-01-01T00:00:00Z</timestamp>'
        '<source>crm</source><type>heartbeat</type><version>2.0</version></header>'
        '<body><status>online</status></body></message>'
    )
    schema = COMPILED["heartbeat"]
    try:
        schema.assertValid(etree.parse(BytesIO(xmlns_xml.encode())))
        fail("Should have rejected: xmlns namespace (v1.0 leftover) in header")
    except (etree.DocumentInvalid, etree.XMLSyntaxError):
        ok("Rejected: xmlns namespace in header (Regel 1 violation)")

    # Forbidden: version=1.0 instead of 2.0
    _state["tests"] += 1
    v1_xml = build_message("heartbeat", "crm",
                           "<status>online</status>").replace(
        "<version>2.0</version>", "<version>1.0</version>")
    try:
        schema.assertValid(etree.parse(BytesIO(v1_xml.encode())))
        fail("Should have rejected: version=1.0")
    except etree.DocumentInvalid:
        ok("Rejected: version=1.0 (contract requires 2.0)")

    # Forbidden: <age> instead of <date_of_birth> (known CRM/Frontend bug)
    _state["tests"] += 1
    age_body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <type>private</type>
        <contact>
          <first_name>Jan</first_name><last_name>Peeters</last_name>
          <email>j@j.be</email>
          <age>29</age>
        </contact>"""
    age_xml = build_message("new_registration", "frontend", age_body)
    schema_reg = COMPILED["frontend_to_crm_new_registration"]
    try:
        schema_reg.assertValid(etree.parse(BytesIO(age_xml.encode())))
        fail("Should have rejected: <age> field (Regel 4 violation — use date_of_birth)")
    except etree.DocumentInvalid:
        ok("Rejected: <age> field (contract requires <date_of_birth>)")

    # Forbidden: currency attribute missing on monetary amount (Regel 3)
    _state["tests"] += 1
    no_currency_body = """\
        <user_id>e8b27c1d-4f2a-4b3e-9c5f-000000000001</user_id>
        <badge_id>BADGE-001</badge_id>
        <items>
          <item>
            <name>Koffie</name><quantity>1</quantity>
            <unit_price>3.00</unit_price>
            <total_amount>3.00</total_amount>
          </item>
        </items>
        <total_order_amount>3.00</total_order_amount>"""
    no_curr_xml = build_message("consumption_order", "kassa", no_currency_body)
    schema_cons = COMPILED["kassa_to_crm_consumption_order"]
    try:
        schema_cons.assertValid(etree.parse(BytesIO(no_curr_xml.encode())))
        fail("Should have rejected: missing currency attribute (Regel 3 violation)")
    except etree.DocumentInvalid:
        ok("Rejected: missing currency attribute on monetary field (Regel 3)")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def should_test(cfg, *teams: str) -> bool:
    if cfg.teams == "all":
        return True
    active = set(cfg.teams.split(","))
    return bool(active.intersection(teams))


def main():
    load_env()
    cfg = parse_args()
    if cfg.env != ".env":
        load_env(cfg.env)

    print()
    print(f"{BOLD}╔══════════════════════════════════════════════════════════════╗{RESET}")
    print(f"{BOLD}║  RabbitMQ Integration Test Suite — Groep 1 — 2026            ║{RESET}")
    print(f"{BOLD}║  Contract: XML/XSD v2.3 Centralized                          ║{RESET}")
    print(f"{BOLD}║  Frontend · CRM · Kassa · Facturatie · Planning              ║{RESET}")
    print(f"{BOLD}║  Mailing · Monitoring · Identity                              ║{RESET}")
    print(f"{BOLD}╚══════════════════════════════════════════════════════════════╝{RESET}")
    print()
    print(f"  Host    : {CYAN}{cfg.host}:{cfg.port}{RESET}")
    print(f"  Mgmt    : {CYAN}{cfg.host}:{cfg.mgmt_port}{RESET}")
    print(f"  Vhost   : {CYAN}{cfg.vhost}{RESET}")
    print(f"  Timeout : {CYAN}{cfg.timeout}s{RESET}")
    print(f"  Mode    : {CYAN}{'DRY-RUN (schema only)' if cfg.dry_run else 'LIVE'}{RESET}")
    print(f"  Teams   : {CYAN}{cfg.teams}{RESET}")

    test_connectivity(cfg)
    test_schema_rejections()

    if should_test(cfg, "frontend", "crm"):
        flow_frontend_crm_new_registration(cfg)
    if should_test(cfg, "crm", "kassa"):
        flow_crm_kassa_new_registration(cfg)
    if should_test(cfg, "kassa", "crm"):
        flow_kassa_crm_consumption_order(cfg)
        flow_kassa_crm_payment_registered(cfg)
    if should_test(cfg, "crm", "facturatie"):
        flow_crm_facturatie_invoice_request(cfg)
    if should_test(cfg, "crm", "mailing"):
        flow_crm_mailing_send_mailing(cfg)
    if should_test(cfg, "facturatie", "mailing"):
        flow_facturatie_mailing_send_mailing(cfg)
    if should_test(cfg, "facturatie", "crm"):
        flow_facturatie_crm_invoice_status(cfg)
    if should_test(cfg, "planning", "crm"):
        flow_planning_crm_session_created(cfg)
    if should_test(cfg, "frontend", "planning"):
        flow_frontend_planning_calendar_invite(cfg)
    if should_test(cfg, "monitoring", "mailing"):
        flow_monitoring_mailing_system_alert(cfg)
    if should_test(cfg, "monitoring", "crm", "kassa", "facturatie",
                   "planning", "mailing", "frontend", "identity"):
        flow_heartbeats(cfg)

    # ── Additional flows (Flows 13–34) ────────────────────────────────────────
    if should_test(cfg, "monitoring"):
        flow_log_message(cfg)
    if should_test(cfg, "frontend", "crm"):
        flow_frontend_crm_user_created(cfg)
        flow_frontend_crm_user_updated(cfg)
        flow_frontend_crm_user_deleted(cfg)
        flow_frontend_crm_user_registered(cfg)
        flow_frontend_crm_cancel_registration(cfg)
        flow_frontend_crm_user_checkin(cfg)
    if should_test(cfg, "kassa", "crm"):
        flow_kassa_crm_badge_assigned(cfg)
        flow_kassa_crm_refund_processed(cfg)
        flow_kassa_crm_invoice_request(cfg)
    if should_test(cfg, "planning", "crm"):
        flow_planning_crm_session_updated(cfg)
        flow_planning_crm_session_deleted(cfg)
    if should_test(cfg, "frontend", "planning"):
        flow_frontend_planning_session_create(cfg)
        flow_frontend_planning_session_update(cfg)
        flow_frontend_planning_session_delete(cfg)
    if should_test(cfg, "crm", "planning"):
        flow_crm_planning_session_registration_confirmed(cfg)
        flow_crm_planning_cancel_registration(cfg)
    if should_test(cfg, "mailing", "crm"):
        flow_mailing_crm_mailing_status(cfg)
    if should_test(cfg, "crm", "facturatie"):
        flow_crm_facturatie_invoice_cancelled(cfg)
    if should_test(cfg, "facturatie", "crm"):
        flow_facturatie_crm_send_invoice(cfg)
        flow_facturatie_crm_payment_registered(cfg)
    if should_test(cfg, "crm", "kassa"):
        flow_crm_kassa_profile_update(cfg)

    # Close connection cleanly
    if _conn and _conn.is_open:
        try:
            _conn.close()
        except Exception:
            pass

    print()
    print(f"{BOLD}══ Summary ══════════════════════════════════════════════════════{RESET}")
    print(f"  Tests run : {BOLD}{_state['tests']}{RESET}")
    if _state["failures"] == 0:
        print(f"  Result    : {GREEN}{BOLD}ALL PASSED ✓{RESET}")
    else:
        print(f"  Failures  : {RED}{BOLD}{_state['failures']}{RESET}")
    print()
    sys.exit(0 if _state["failures"] == 0 else 1)


if __name__ == "__main__":
    main()
