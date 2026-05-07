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
    p.add_argument("--host",      default=os.getenv("RABBIT_HOST", "localhost"))
    p.add_argument("--port",      type=int, default=int(os.getenv("RABBIT_PORT", 5672)))
    p.add_argument("--user",      default=os.getenv("RABBIT_USER", "guest"))
    p.add_argument("--pass",      dest="password", default=os.getenv("RABBIT_PASS", "guest"))
    p.add_argument("--mgmt-port", type=int, default=int(os.getenv("RABBIT_MGMT_PORT", 15672)))
    p.add_argument("--vhost",     default=os.getenv("RABBIT_VHOST", "/"))
    p.add_argument("--timeout",   type=int, default=int(os.getenv("TIMEOUT", 5)))
    p.add_argument("--teams",     default="all")
    p.add_argument("--dry-run",   action="store_true")
    p.add_argument("--verbose",   action="store_true")
    p.add_argument("--env",       default=".env")
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


def get_channel(cfg):
    global _conn, _channel
    if _conn and _conn.is_open:
        return _channel
    creds = pika.PlainCredentials(cfg.user, cfg.password)
    params = pika.ConnectionParameters(
        host=cfg.host, port=cfg.port, virtual_host=cfg.vhost,
        credentials=creds, socket_timeout=5,
        connection_attempts=3, retry_delay=1
    )
    _conn = pika.BlockingConnection(params)
    _channel = _conn.channel()
    return _channel


def publish(cfg, exchange: str, routing_key: str, xml: str) -> bool:
    if cfg.dry_run:
        info(f"DRY-RUN publish → exchange='{exchange}' rk='{routing_key}'")
        return True
    try:
        ch = get_channel(cfg)
        if exchange:
            try:
                ch.exchange_declare(exchange=exchange, exchange_type="topic",
                                    durable=True, passive=True)
            except Exception:
                global _conn, _channel
                _conn.close()
                _conn = None
                ch = get_channel(cfg)
                ch.exchange_declare(exchange=exchange, exchange_type="topic", durable=True)
        else:
            try:
                ch.queue_declare(queue=routing_key, durable=True, passive=True)
            except Exception:
                _conn.close()
                _conn = None
                ch = get_channel(cfg)
                ch.queue_declare(queue=routing_key, durable=True)

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
        ok(f"Arrived     : queue='{queue}' type={expected_type} — {label}")
    else:
        fail(f"NOT ARRIVED : queue='{queue}' type={expected_type} — {label}")


# ── Test runner helper ─────────────────────────────────────────────────────────
def run_flow(cfg, schema_name: str, label: str,
             xml: str, exchange: str, routing_key: str, arrival_queue: str):
    if cfg.verbose:
        print(f"\n{CYAN}--- XML ---{RESET}\n{xml}\n")
    valid = validate(xml, schema_name, label)
    if not valid:
        warn("Skipping publish — XML failed schema validation")
        return
    if publish(cfg, exchange, routing_key, xml):
        peek_queue(cfg, arrival_queue, xml.split("<type>")[1].split("</type>")[0], label)


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
    for team in teams:
        body = f"""\
        <status>online</status>
        <uptime>60</uptime>"""
        xml = build_message("heartbeat", team, body)
        if cfg.verbose:
            print(f"\n{CYAN}--- heartbeat ({team}) ---{RESET}\n{xml}\n")
        validate(xml, "heartbeat", f"heartbeat from {team}")
        if publish(cfg, "", "heartbeat", xml):
            pass  # check once below
    peek_queue(cfg, "heartbeat", "heartbeat",
               "Any heartbeat arrived in monitoring queue")


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
