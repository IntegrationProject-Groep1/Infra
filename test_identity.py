import pika
import uuid
import os
import xml.etree.ElementTree as ET

# Connection settings
# Credentials are read from environment variables — never hardcode them here.
# Run this script as:
#   IDENTITY_RABBITMQ_USER=identity_user \
#   IDENTITY_RABBITMQ_PASS=your_password \
#   RABBITMQ_HOST=rabbitmq_broker \
#   python test_identity.py
user = os.getenv('IDENTITY_RABBITMQ_USER')
password = os.getenv('IDENTITY_RABBITMQ_PASS')
host = os.getenv('RABBITMQ_HOST')

if not all([user, password, host]):
    raise EnvironmentError("Set IDENTITY_RABBITMQ_USER, IDENTITY_RABBITMQ_PASS and RABBITMQ_HOST before running.")

credentials = pika.PlainCredentials(user, password)
parameters = pika.ConnectionParameters(host=host, credentials=credentials)
connection = pika.BlockingConnection(parameters)
channel = connection.channel()

# Create a unique reply queue
result = channel.queue_declare(queue='', exclusive=True)
callback_queue = result.method.queue

# XML payload to create a new user
corr_id = str(uuid.uuid4())
xml_request = f"""<?xml version="1.0" encoding="UTF-8"?>
<identity_request>
    <email>test.user_{corr_id[:8]}@ehb.be</email>
    <source_system>test_script</source_system>
</identity_request>"""

print(f"[*] Sending request to create new user...")

# Send the request
channel.basic_publish(
    exchange='',
    routing_key='identity.user.create.request',
    properties=pika.BasicProperties(
        reply_to=callback_queue,
        correlation_id=corr_id,
        content_type='application/xml'
    ),
    body=xml_request
)

# Wait for response
def on_response(ch, method, props, body):
    if corr_id == props.correlation_id:
        print("[v] Response received from Identity Service:")
        print(body.decode())
        connection.close()

channel.basic_consume(queue=callback_queue, on_message_callback=on_response, auto_ack=True)
channel.start_consuming()
