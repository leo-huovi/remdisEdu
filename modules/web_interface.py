# web_interface.py
import sys
import threading
import queue
import time
import json

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO

import pika
import pika.exceptions

class WebInterface:
    def __init__(self, host='localhost'):
        # Flask setup
        self.app = Flask(__name__, template_folder='templates', static_folder='static')
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")

        # RabbitMQ connection parameters
        self.host = host
        # Define exchanges this interface interacts with
        self.exchanges = ['dialogue', 'dialogue2', 'asr', 'emo_act'] # Ensure emo_act is listed

        # Message queues for incoming messages from RabbitMQ
        self.message_queues = {ex: queue.Queue() for ex in self.exchanges}

        # Separate connection for publishing
        self.publish_connection = None
        self.publish_channel = None
        self.publish_lock = threading.Lock()

        # State tracking (optional, for display consistency)
        self.system_expression = "normal"
        self.system_action = "wait"

        # ASR text input simulation state
        self.last_input_time = 0
        self.input_buffer = ""
        self.input_timeout = 1.5 # seconds

        # Setup Flask routes and SocketIO events
        self.setup_routes()

        self.log("WebInterface initialized")

    def setup_routes(self):
        """Sets up Flask routes and SocketIO event handlers."""
        @self.app.route('/')
        def index(): return render_template('index.html')
        @self.socketio.on('connect')
        def handle_connect(): self.log("Client connected"); self.socketio.emit('status', {'status': 'connected'})
        @self.socketio.on('disconnect')
        def handle_disconnect(): self.log("Client disconnected")
        @self.socketio.on('user_input')
        def handle_user_input(data): self.handle_asr_simulation(data.get('text', ''), data.get('is_final', False))

    def run(self):
        """Starts the WebInterface server and background threads."""
        self.log("Starting WebInterface...")
        self.init_rabbitmq()
        threading.Thread(target=self.process_message_queues, daemon=True).start()
        threading.Thread(target=self.check_input_timeout, daemon=True).start()
        self.log("Starting Flask-SocketIO server on 0.0.0.0:8080")
        self.socketio.run(self.app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)

    def init_rabbitmq(self):
        """Sets up RabbitMQ connections: one publisher and consumer threads."""
        try:
            # --- Setup Publisher ---
            self.log("Setting up RabbitMQ publisher...")
            self.publish_connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.host))
            self.publish_channel = self.publish_connection.channel()
            for exchange in self.exchanges:
                self.publish_channel.exchange_declare(exchange=exchange, exchange_type='fanout', durable=False)
            self.log("Publisher connection established.")

            # --- Setup Consumers ---
            self.log("Setting up RabbitMQ consumers...")
            # --- DEBUG: Explicitly list subscribed exchanges ---
            exchanges_to_subscribe = ['dialogue', 'dialogue2', 'asr', 'emo_act'] # Make sure emo_act is here
            self.log(f"Will subscribe to: {exchanges_to_subscribe}")
            # ----------------------------------------------------
            for exchange in exchanges_to_subscribe:
                 if exchange not in self.message_queues: self.message_queues[exchange] = queue.Queue()
                 consumer_thread = threading.Thread( target=self.setup_consumer, args=(exchange,), daemon=True)
                 consumer_thread.start()
                 # self.log(f"Consumer thread started for exchange: {exchange}")

            self.log("RabbitMQ initialization complete.")
        except pika.exceptions.AMQPConnectionError as e: self.log(f"FATAL: RabbitMQ connection error: {e}"); sys.exit(1)
        except Exception as e: self.log(f"ERROR during RabbitMQ initialization: {e}"); sys.exit(1)

    def setup_consumer(self, exchange):
        """Sets up and runs a consumer for a specific exchange in its own thread."""
        while True:
            try:
                connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.host))
                channel = connection.channel()
                channel.exchange_declare(exchange=exchange, exchange_type='fanout', durable=False)
                result = channel.queue_declare(queue='', exclusive=True)
                queue_name = result.method.queue
                channel.queue_bind(exchange=exchange, queue=queue_name)
                on_message_callback = lambda ch, method, properties, body: self.on_message(ch, method, properties, body, exchange)
                channel.basic_consume(queue=queue_name, on_message_callback=on_message_callback, auto_ack=True)
                self.log(f"Consumer ready for exchange: {exchange}")
                channel.start_consuming()
            except (pika.exceptions.ConnectionClosedByBroker, pika.exceptions.AMQPChannelError,
                    pika.exceptions.AMQPConnectionError) as e:
                self.log(f"Consumer connection/channel error ({type(e).__name__}) on exchange {exchange}. Reconnecting...")
            except Exception as e: self.log(f"Unexpected error in consumer for {exchange}: {e}. Reconnecting...")
            time.sleep(5)

    def on_message(self, ch, method, properties, body, exchange):
        """Callback triggered when a message is received from RabbitMQ."""
        try:
            # --- DEBUG LOG ---
            if exchange == 'emo_act':
                self.log(f"RECEIVED message on 'emo_act': {body.decode('utf-8', errors='ignore')[:100]}...")
            # -----------------
            message_str = body.decode('utf-8'); message = json.loads(message_str)
            if exchange in self.message_queues: self.message_queues[exchange].put(message)
            else: self.log(f"Warning: Received message from unhandled exchange: {exchange}")
        except Exception as e: self.log(f"Error processing message from exchange '{exchange}': {e}")

    def process_message_queues(self):
        """Continuously processes messages from internal queues and updates web clients via SocketIO."""
        self.log("Starting message queue processor...")
        while True:
            try:
                processed_message = False
                for exchange, msg_queue in self.message_queues.items():
                    try:
                        while not msg_queue.empty():
                            message = msg_queue.get(block=False)
                            # --- DEBUG LOG ---
                            # self.log(f"Processing message from queue '{exchange}'...")
                            # -----------------
                            if exchange == 'dialogue': self.process_dialogue_message(message)
                            elif exchange == 'dialogue2' or exchange == 'emo_act': # Route emo_act here
                                self.process_dialogue2_message(message)
                            elif exchange == 'asr': self.process_asr_message(message)
                            processed_message = True; msg_queue.task_done()
                    except queue.Empty: pass
                    except Exception as e: self.log(f"Error processing message from queue '{exchange}': {e}")
                if not processed_message: time.sleep(0.05)
            except Exception as e: self.log(f"FATAL Error in process_message_queues loop: {e}"); time.sleep(1)

    # --- Message Processors ---
    def process_dialogue_message(self, message):
        """Handles messages from the 'dialogue' exchange."""
        update_type = message.get('update_type', ''); body = message.get('body', '')
        if update_type == 'add' and body:
            # self.log(f"Processing dialogue ADD: {body}")
            self.socketio.emit('new_text', {'text': body, 'role': 'system'})
        elif update_type == 'commit':
            # self.log("Processing dialogue COMMIT (system finished speaking)")
            self.socketio.emit('system_finished_speaking')

    def process_dialogue2_message(self, message):
        """Handles messages from 'dialogue2' OR 'emo_act' (system state)."""
        # --- DEBUG LOG ---
        self.log(f"Entering process_dialogue2_message with message from exchange '{message.get('exchange', 'unknown')}': {message}")
        # -----------------
        if isinstance(message.get('body'), dict):
             body = message.get('body', {})
             # self.log(f"  Processing dialogue2/emo_act body: {body}")
             expression = body.get('expression', self.system_expression)
             action = body.get('action', self.system_action)
             concept = body.get('concept', '')
             current_text = body.get('current_text', '')
             progress = body.get('speech_progress', None)
             self.system_expression = expression; self.system_action = action
             payload_to_emit = { 'expression': expression, 'action': action, 'concept': concept,
                                 'current_text': current_text, 'progress': progress }
             # --- DEBUG LOG ---
             self.log(f"  Emitting 'system_state': {payload_to_emit}")
             # -----------------
             self.socketio.emit('system_state', payload_to_emit)
        else: self.log(f"Ignoring dialogue2/emo_act message with unexpected body: {message}")

    def process_asr_message(self, message):
        """Handles messages from the 'asr' exchange (feedback loop)."""
        update_type = message.get('update_type', ''); body = message.get('body', '')
        if update_type == 'add': self.socketio.emit('asr_token', {'text': body, 'stability': message.get('stability', 0.0)})
        elif update_type == 'commit': self.log(f"Processing ASR COMMIT received (feedback): {body}"); self.socketio.emit('user_finished_speaking')
        elif update_type == 'revoke': self.log(f"Processing ASR REVOKE received (feedback)"); self.socketio.emit('asr_revoked')

    # --- ASR Simulation Logic ---
    def handle_asr_simulation(self, text, is_final):
        """Handles input from the web text box, simulating ASR messages."""
        current_time = time.time(); self.last_input_time = current_time
        if is_final:
            final_text_to_send = text
            # self.log(f"Handling FINAL simulated input: '{final_text_to_send}'")
            message = { 'timestamp': current_time, 'id': f"user-final-{int(current_time)}", 'producer': 'WebInterface_Sim',
                        'update_type': 'commit', 'exchange': 'asr', 'body': final_text_to_send, 'stability': 1.0, 'confidence': 1.0 }
            self.publish_to_rabbitmq('asr', message)
            self.input_buffer = ""
        else:
            if text != self.input_buffer:
                # self.log(f"Handling PARTIAL simulated input: '{text}'")
                message = { 'timestamp': current_time, 'id': f"user-partial-{int(current_time)}", 'producer': 'WebInterface_Sim',
                            'update_type': 'add', 'exchange': 'asr', 'body': text, 'stability': 0.5, 'confidence': 0.5 }
                self.publish_to_rabbitmq('asr', message)
                self.input_buffer = text

    def check_input_timeout(self):
        """Periodically checks if the user has stopped typing."""
        # self.log("Starting input timeout checker...")
        while True:
            try:
                current_time = time.time()
                if self.input_buffer and (current_time - self.last_input_time) >= self.input_timeout:
                    text_at_timeout = self.input_buffer
                    self.log(f"Input timeout detected ({self.input_timeout}s). Auto-committing: '{text_at_timeout}'")
                    self.handle_asr_simulation(text_at_timeout, True) # Treat as final input
            except Exception as e: self.log(f"Error in input timeout check: {e}")
            time.sleep(0.2)

    # --- RabbitMQ Publisher ---
    def publish_to_rabbitmq(self, exchange, message):
        """Publishes a message to a specified RabbitMQ exchange."""
        if not self.publish_channel or not self.publish_connection or not self.publish_connection.is_open:
            self.log("Publisher connection lost. Attempting to reconnect...")
            try:
                self.publish_connection = pika.BlockingConnection(pika.ConnectionParameters(host=self.host))
                self.publish_channel = self.publish_connection.channel()
                for ex in self.exchanges: self.publish_channel.exchange_declare(exchange=ex, exchange_type='fanout', durable=False)
                self.log("Publisher reconnected.")
            except Exception as e: self.log(f"ERROR: Failed to reconnect publisher: {e}"); return

        try:
            with self.publish_lock:
                self.publish_channel.basic_publish(
                    exchange=exchange, routing_key='', body=json.dumps(message),
                    properties=pika.BasicProperties(content_type='application/json', delivery_mode=1))
            # self.log(f"Published message to exchange '{exchange}'.")
        except pika.exceptions.AMQPConnectionError: self.log(f"Error publishing (Connection Error)."); self.close_publisher()
        except Exception as e: self.log(f"Error publishing message to exchange '{exchange}': {e}")

    def close_publisher(self):
        """Helper to close publisher connection."""
        if self.publish_connection and self.publish_connection.is_open:
            try: self.publish_connection.close()
            except: pass
        self.publish_connection = None; self.publish_channel = None

    def log(self, message):
        """Simple prefixed logging function."""
        sys.stdout.write(f"[WebInterface {time.time():.3f}] {message}\n"); sys.stdout.flush()

# --- Main Execution ---
def main():
    try: interface = WebInterface(); interface.run()
    except Exception as e: print(f"FATAL ERROR running WebInterface: {e}"); import traceback; print(traceback.format_exc())
if __name__ == '__main__': main()
