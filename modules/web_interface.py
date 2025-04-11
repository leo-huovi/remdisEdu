import sys
import threading
import queue
import time
import json

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

import pika
from pika.adapters.select_connection import SelectConnection

class WebInterface:
    def __init__(self, host='localhost'):
        # Flask setup
        self.app = Flask(__name__,
                         template_folder='templates',
                         static_folder='static')
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")

        # RabbitMQ connection parameters
        self.host = host
        self.exchanges = ['dialogue', 'dialogue2', 'asr']

        # Message queues for incoming messages from RabbitMQ
        self.message_queues = {ex: queue.Queue() for ex in self.exchanges}

        # Separate connection for publishing
        self.publish_connection = None
        self.publish_channel = None

        # Conversation tracking
        self.dialogue_history = []
        self.current_user_utterance = ""
        self.system_state = "idle"
        self.system_expression = "normal"
        self.system_action = "waiting"

        # ASR text input simulation
        self.last_input_time = 0
        self.input_buffer = ""
        self.input_timeout = 3.0  # seconds

        # Setup routes
        @self.app.route('/')
        def index():
            return render_template('index.html')

        @self.socketio.on('connect')
        def handle_connect():
            print("Client connected")
            self.socketio.emit('status', {'status': 'connected'})

        @self.socketio.on('disconnect')
        def handle_disconnect():
            print("Client disconnected")

        @self.socketio.on('user_input')
        def handle_user_input(data):
            self.handle_asr_simulation(data['text'], data['is_final'])

        self.log("WebInterface initialized")

    def run(self):
        self.log("Starting WebInterface...")

        # Initialize RabbitMQ connections
        self.init_rabbitmq()

        # Start message processing thread
        processor_thread = threading.Thread(target=self.process_message_queues, daemon=True)
        processor_thread.start()

        # Start ASR simulation monitoring
        timeout_thread = threading.Thread(target=self.check_input_timeout, daemon=True)
        timeout_thread.start()

        # Run Flask server in the main thread
        self.socketio.run(self.app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)

    def init_rabbitmq(self):
        """Set up separate connections for consuming and publishing"""
        try:
            # Create dedicated publishing connection
            self.publish_connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=self.host))
            self.publish_channel = self.publish_connection.channel()

            # Declare exchanges on publish channel
            for exchange in self.exchanges:
                self.publish_channel.exchange_declare(exchange=exchange, exchange_type='fanout')

            self.log("Set up publishing connection to RabbitMQ")

            # Set up consumer connections - one thread per exchange
            for exchange in self.exchanges:
                # Start a dedicated thread for each exchange's consumer
                consumer_thread = threading.Thread(
                    target=self.setup_consumer,
                    args=(exchange,),
                    daemon=True
                )
                consumer_thread.start()

            self.log("Started consumer threads for RabbitMQ")
        except Exception as e:
            self.log(f"Failed to initialize RabbitMQ: {e}")

    def setup_consumer(self, exchange):
        """Set up a consumer for a specific exchange in its own thread"""
        try:
            # Create a dedicated connection for this consumer
            connection = pika.BlockingConnection(
                pika.ConnectionParameters(host=self.host))
            channel = connection.channel()

            # Declare the exchange and bind a queue
            channel.exchange_declare(exchange=exchange, exchange_type='fanout')
            result = channel.queue_declare(queue='', exclusive=True)
            queue_name = result.method.queue
            channel.queue_bind(exchange=exchange, queue=queue_name)

            # Set up the consumer
            channel.basic_consume(
                queue=queue_name,
                on_message_callback=lambda ch, method, properties, body:
                    self.on_message(ch, method, properties, body, exchange),
                auto_ack=True
            )

            self.log(f"Consumer ready for exchange: {exchange}")

            # Start consuming - this will block this thread
            channel.start_consuming()
        except Exception as e:
            self.log(f"Consumer error on exchange {exchange}: {e}")
            # Try to reconnect after a delay
            time.sleep(5)
            self.setup_consumer(exchange)

    def on_message(self, ch, method, properties, body, exchange):
        """Callback for incoming messages from RabbitMQ"""
        try:
            # Parse the message and add to the appropriate queue
            message = json.loads(body)
            self.message_queues[exchange].put(message)
            self.log(f"Received message from {exchange}: {message.get('update_type', 'unknown')}")
        except Exception as e:
            self.log(f"Error processing message: {e}")

    def process_message_queues(self):
        """Process messages from all queues and send to web clients"""
        while True:
            try:
                # Check all message queues
                for exchange, msg_queue in self.message_queues.items():
                    try:
                        while not msg_queue.empty():
                            message = msg_queue.get(block=False)

                            # Process based on the exchange
                            if exchange == 'dialogue':
                                self.process_dialogue_message(message)
                            elif exchange == 'dialogue2':
                                self.process_dialogue2_message(message)
                            elif exchange == 'asr':
                                self.process_asr_message(message)
                    except queue.Empty:
                        pass
            except Exception as e:
                self.log(f"Error in message processing: {e}")

            # Sleep to avoid high CPU usage
            time.sleep(0.1)

    def process_dialogue_message(self, message):
        """Process system utterances"""
        update_type = message.get('update_type', '')

        if update_type == 'add' and 'body' in message:
            # System utterance
            self.log(f"System says: {message['body']}")
            self.socketio.emit('new_text', {
                'text': message['body'],
                'role': 'system'
            })

            # Track in history
            if len(self.dialogue_history) > 0 and self.dialogue_history[-1]['role'] == 'system':
                self.dialogue_history[-1]['text'] += message['body']
            else:
                self.dialogue_history.append({
                    'role': 'system',
                    'text': message['body']
                })

        elif update_type == 'commit':
            # Signal end of system utterance
            self.log("System finished speaking")
            self.socketio.emit('system_finished_speaking')

    def process_dialogue2_message(self, message):
        """Process system expression and action updates"""
        if message.get('data_type') == 'expression_and_action':
            body = message.get('body', {})
            self.log(f"Expression/action: {body}")

            # Update internal state
            if 'expression' in body:
                self.system_expression = body['expression']
            if 'action' in body:
                self.system_action = body['action']

            # Send update to frontend
            self.socketio.emit('system_state', {
                'expression': self.system_expression,
                'action': self.system_action,
                'progress': body.get('speech_progress', None)
            })

    def process_asr_message(self, message):
        """Process ASR results"""
        update_type = message.get('update_type', '')

        if update_type == 'add':
            # Add token to current utterance
            self.log(f"ASR token: {message.get('body', '')}")
            self.socketio.emit('asr_token', {
                'text': message.get('body', ''),
                'stability': message.get('stability', 0.0)
            })

        elif update_type == 'commit':
            # End of user utterance
            self.log(f"User finished speaking: {self.current_user_utterance}")
            final_text = self.current_user_utterance.strip()

            # Add to dialogue history
            self.dialogue_history.append({
                'role': 'user',
                'text': final_text
            })

            # Send to frontend
            self.socketio.emit('new_text', {
                'text': final_text,
                'role': 'user'
            })

            # Reset current utterance
            self.current_user_utterance = ""
            self.socketio.emit('user_finished_speaking')

        elif update_type == 'revoke':
            # Clear the current utterance
            self.current_user_utterance = ""
            self.socketio.emit('asr_revoked')

    def handle_asr_simulation(self, text, is_final):
        """Handle simulated ASR input from the web interface"""
        self.last_input_time = time.time()

        if is_final:
            # User finished typing (pressed Enter or timeout)
            self.log(f"Final ASR input: {text}")

            # Create final ASR message
            self.publish_to_rabbitmq('asr', {
                'timestamp': time.time(),
                'id': f"user-{int(time.time())}",
                'producer': 'WebInterface',
                'update_type': 'commit',
                'exchange': 'asr',
                'body': text,
                'stability': 1.0,
                'confidence': 1.0
            })

            # Reset input buffer
            self.input_buffer = ""
        else:
            # Partial input (user still typing)
            if text != self.input_buffer:
                self.log(f"Partial ASR input: {text}")

                # Send only the new part
                new_text = text[len(self.input_buffer):] if text.startswith(self.input_buffer) else text

                # Create partial ASR message
                self.publish_to_rabbitmq('asr', {
                    'timestamp': time.time(),
                    'id': f"user-{int(time.time())}-partial",
                    'producer': 'WebInterface',
                    'update_type': 'add',
                    'exchange': 'asr',
                    'body': new_text,
                    'stability': 0.5,
                    'confidence': 0.5
                })

                # Update input buffer
                self.input_buffer = text

    def check_input_timeout(self):
        """Check if input has timed out (user stopped typing)"""
        while True:
            try:
                # Check if we have input and if it's been idle long enough
                current_time = time.time()
                if self.input_buffer and (current_time - self.last_input_time) > self.input_timeout:
                    # Input has timed out, treat as final
                    self.log(f"Input timeout - treating as final: {self.input_buffer}")

                    # Handle as final input
                    self.handle_asr_simulation(self.input_buffer, True)
            except Exception as e:
                self.log(f"Error in input timeout check: {e}")

            # Check every half second
            time.sleep(0.5)

    def publish_to_rabbitmq(self, exchange, message):
        """Publish a message to RabbitMQ"""
        try:
            # Make sure we have a valid publishing connection and channel
            if self.publish_connection is None or not self.publish_connection.is_open:
                self.log("Reconnecting publisher...")
                self.publish_connection = pika.BlockingConnection(
                    pika.ConnectionParameters(host=self.host))
                self.publish_channel = self.publish_connection.channel()

                # Redeclare exchanges
                for ex in self.exchanges:
                    self.publish_channel.exchange_declare(exchange=ex, exchange_type='fanout')

            # Publish the message
            self.publish_channel.basic_publish(
                exchange=exchange,
                routing_key='',
                body=json.dumps(message)
            )

            self.log(f"Published message to {exchange}")
        except Exception as e:
            self.log(f"Error publishing to RabbitMQ: {e}")
            # Try to reset the connection for next time
            try:
                if self.publish_connection and self.publish_connection.is_open:
                    self.publish_connection.close()
            except:
                pass
            self.publish_connection = None
            self.publish_channel = None

    def log(self, message):
        """Simple logging function"""
        print(f"[WebInterface] {message}", flush=True)

def main():
    interface = WebInterface()
    interface.run()

if __name__ == '__main__':
    main()
