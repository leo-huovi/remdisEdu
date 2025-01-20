import sys, os
import numpy
import queue

import time

import threading
import base64
import librosa

from base import RemdisModule, RemdisUpdateType

import torch
device = torch.device("cpu")

class TTS(RemdisModule):
    def __init__(self, 
                 pub_exchanges=['tts'],
                 sub_exchanges=['dialogue']):
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        self.org_rate = self.config['TTS']['org_sample_rate']
        self.dst_rate = self.config['TTS']['dst_sample_rate']
        self.scale_factor = self.config['TTS']['scale_factor']
        self.frame_length = self.config['TTS']['frame_length']
        self.send_interval = self.config['TTS']['send_interval']
        self.sample_width = self.config['TTS']['sample_width']
        self.chunk_size = round(self.frame_length * self.dst_rate)

        self.input_iu_buffer = queue.Queue()
        self.output_iu_buffer = queue.Queue()
        self.engine_name = self.config['TTS']['engine_name']
        self.model_name = self.config['TTS']['model_name']

        # Selection of speech synthesis engine
        self.engine = None
        if self.engine_name == 'ttslearn':
            from ttslearn.pretrained import create_tts_engine
            self.engine = create_tts_engine(self.model_name,
                                            device=device)
        elif self.engine_name == 'TTS':
            from TTS.api import TTS
            self.engine = TTS(self.model_name).to(device)
        elif self.engine_name == 'openjtalk':
            import pyopenjtalk
            self.engine = pyopenjtalk
        
        self.is_revoked = False
        self._is_running = True

    def run(self):
        # Message receiving thread
        t1 = threading.Thread(target=self.listen_loop)
        # Speech synthesis processing thread
        t2 = threading.Thread(target=self.synthesis_loop)
        # Message sending thread
        t3 = threading.Thread(target=self.send_loop)

        # Execute threads
        t1.start()
        t2.start()
        t3.start()

        t1.join()
        t2.join()
        t3.join()

    def listen_loop(self):
        self.subscribe('dialogue', self.callback)

    def send_loop(self):
        # Send audio data in chunks
        while True:
            # Stop transmission if REVOKE is received (Process when user interrupt occurs)
            if self.is_revoked:
                self.output_iu_buffer = queue.Queue()
                self.send_commitIU('tts')
                
            snd_iu = self.output_iu_buffer.get(block=True)
            self.publish(snd_iu, 'tts')

            # Execute transmission at intervals between chunks (Transmit slightly faster to avoid sound break)
            time.sleep(self.send_interval)

            # Process when the system speech end is reached
            if snd_iu['update_type'] == RemdisUpdateType.COMMIT:
                self.send_commitIU('tts')

    def synthesis_loop(self):
        while True:
            if self.is_revoked:
                self.input_iu_buffer = queue.Queue()

            # Get IU received from input buffer
            in_msg = self.input_iu_buffer.get(block=True)
            output_text = in_msg['body']
            tgt_id = in_msg['id']
            update_type = in_msg['update_type']

            x = numpy.array([])
            sr = 0
            sleep_time = 0

            if output_text != '':
                # Speech synthesis
                x = self.engine.tts(output_text)
                if len(x) == 2:
                    x, _ = x
                x = numpy.array(x) * self.scale_factor
                x = x.astype(numpy.int16)

                # Downsample audio to fit MMDAgent-EX specifications
                x = librosa.resample(x.astype(numpy.float32),
                                     orig_sr=self.org_rate,
                                     target_sr=self.dst_rate)

                # Divide into chunks and store in output buffer
                t = 0
                if self.is_revoked == True:
                    continue

                while t <= len(x):
                    chunk = x[t:t+self.chunk_size]
                    chunk = base64.b64encode(chunk.astype(numpy.int16).tobytes()).decode('utf-8')
                    snd_iu = self.createIU(chunk, 'tts',
                                           update_type)
                    snd_iu['data_type'] = 'audio'
                    self.output_iu_buffer.put(snd_iu)
                    t += self.chunk_size
            else:
                # Also execute process when there is no text
                x = numpy.zeros(self.chunk_size)
                chunk = base64.b64encode(x.astype(numpy.int16).tobytes()).decode('utf-8')
                snd_iu = self.createIU(chunk, 'tts',
                                       update_type)
                snd_iu['data_type'] = 'audio'
                self.output_iu_buffer.put(snd_iu)

    # Message sending function at the end of speech
    def send_commitIU(self, channel):
        snd_iu = self.createIU('', channel,
                               RemdisUpdateType.COMMIT)
        snd_iu['data_type'] = 'audio'
        self.printIU(snd_iu)
        self.publish(snd_iu, channel)

    # Callback function for message reception
    def callback(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        self.printIU(in_msg)
        
        # Monitor the update_type of system speech
        if in_msg['update_type'] == RemdisUpdateType.REVOKE:
            self.is_revoked = True
        else:
            self.input_iu_buffer.put(in_msg)
            self.is_revoked = False

def main():
    tts = TTS()
    tts.run()

if __name__ == '__main__':
    main()
