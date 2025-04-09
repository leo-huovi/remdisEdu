import sys
import threading
import queue
import time
import json

from flask import Flask, render_template
from flask_socketio import SocketIO

from base import RemdisModule, RemdisUpdateType

class WebInterface(RemdisModule):
    def __init__(self,
                 sub_exchanges=['dialogue', 'dialogue2', 'asr']):
        super().__init__(sub_exchanges=sub_exchanges)

        # Flask setup
        self.app = Flask(__name__,
                         template_folder='web_templates',
                         static_folder='web_static')
        self.socketio = SocketIO(self.app, cors_allowed_origins="*")

        # Conversation tracking
        self.dialogue_history = []
        self.current_user_utterance = ""
        self.system_state = "idle"
        self.system_expression = "normal"
        self.system_action = "waiting"

        # Setup routes
        @self.app.route('/')
        def index():
            return render_template('index.html')

        self.log("WebInterface initialized")

    def run(self):
        self.log("Starting WebInterface...")
        # Start message listening threads
        t1 = threading.Thread(target=self.listen_dialogue_loop)
        t2 = threading.Thread(target=self.listen_dialogue2_loop)
        t3 = threading.Thread(target=self.listen_asr_loop)
        t4 = threading.Thread(target=self.run_web_server)

        t1.start()
        t2.start()
        t3.start()
        t4.start()

    def listen_dialogue_loop(self):
        self.log("Subscribing to dialogue exchange")
        self.subscribe('dialogue', self.callback_dialogue)

    def listen_dialogue2_loop(self):
        self.log("Subscribing to dialogue2 exchange")
        self.subscribe('dialogue2', self.callback_dialogue2)

    def listen_asr_loop(self):
        self.log("Subscribing to asr exchange")
        self.subscribe('asr', self.callback_asr)

    def run_web_server(self):
        self.log("Starting web server on port 8080")
        self.socketio.run(self.app, host='0.0.0.0', port=8080, debug=False, allow_unsafe_werkzeug=True)

    def callback_dialogue(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.log(f"Dialogue message: {in_msg['update_type']}")

        if in_msg['update_type'] == RemdisUpdateType.ADD and 'body' in in_msg:
            # For text messages, send to frontend as a permanent transcript entry.
            self.log(f"System says: {in_msg['body']}")
            self.socketio.emit('new_text', {
                'text': in_msg['body'],
                'role': 'system'
            })

            # Track in history
            if len(self.dialogue_history) > 0 and self.dialogue_history[-1]['role'] == 'system':
                self.dialogue_history[-1]['text'] += in_msg['body']
            else:
                self.dialogue_history.append({
                    'role': 'system',
                    'text': in_msg['body']
                })

        elif in_msg['update_type'] == RemdisUpdateType.COMMIT:
            # Signal end of system utterance
            self.log("System finished speaking")
            self.socketio.emit('system_finished_speaking')

    def callback_dialogue2(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)

        if in_msg['data_type'] == 'expression_and_action':
            body = in_msg['body']
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

    def callback_asr(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)

        if in_msg['update_type'] == RemdisUpdateType.ADD:
            # For ASR results, send partial update to frontend
            self.log(f"ASR token: {in_msg['body']}")
            self.socketio.emit('asr_token', {
                'text': in_msg['body'],
                'stability': in_msg.get('stability', 0.0)
            })
            # Build up current user utterance from partial tokens
            self.current_user_utterance += in_msg['body'] + " "
            # Emit a live (partial) user update so the transcript shows ongoing input
            self.socketio.emit('partial_user', {
                'text': self.current_user_utterance.strip()
            })

        elif in_msg['update_type'] == RemdisUpdateType.COMMIT:
            # End of user utterance: finalize the current input.
            self.log(f"User finished speaking: {self.current_user_utterance}")
            final_text = self.current_user_utterance.strip()
            # Add finalized utterance to dialogue history
            self.dialogue_history.append({
                'role': 'user',
                'text': final_text
            })
            # Emit a new_text event so the transcript permanently displays the user turn
            self.socketio.emit('new_text', {
                'text': final_text,
                'role': 'user'
            })
            # Reset the current user utterance
            self.current_user_utterance = ""
            # Notify clients to clear any temporary display (e.g. partial text)
            self.socketio.emit('user_finished_speaking')

        elif in_msg['update_type'] == RemdisUpdateType.REVOKE:
            # ASR correction: clear the accumulated utterance and notify the frontend.
            self.log("ASR revoked")
            self.current_user_utterance = ""
            self.socketio.emit('asr_revoked')

    def log(self, message):
        print(f"[WebInterface] {message}", flush=True)

def main():
    web_interface = WebInterface()
    web_interface.run()

if __name__ == '__main__':
    main()
