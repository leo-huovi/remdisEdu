import sys, os
import time
import numpy
import queue

import threading
import base64, copy

import torch
import torch.nn as nn
from _audio_vap.VAP import VAP
from _audio_vap.encoder import EncoderCPC
from _audio_vap.modules import TransformerStereo

from scipy.io.wavfile import write

from base import RemdisModule, RemdisUpdateType

from vap.utils.audio import load_waveform

# GPU settings
if sys.platform == 'darwin':
    # Mac
    device = torch.device('mps' if torch.backends.mps.is_available() else 'cpu')
else:
    # Windows/Linux
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu') 

class Audio_VAP(RemdisModule):
    def __init__(self, 
                 pub_exchanges=['vap', 'score'],
                 sub_exchanges=['ain', 'tts']):
        super().__init__(pub_exchanges=pub_exchanges,
                         sub_exchanges=sub_exchanges)

        # Variables for VAP
        self.model_name = self.config['VAP']['model_filename']
        self.buffer_length = self.config['VAP']['buffer_length']
        self.threshold = self.config['VAP']['threshold']
        self.sample_rate = self.config['TTS']['dst_sample_rate']
        self.buffer_size = int(self.buffer_length * self.sample_rate)
        self.tts_frame_length = self.config['TTS']['frame_length']

        self.us_audio_buffer = numpy.zeros(self.buffer_size,
                                           dtype=numpy.float32)
        self.ss_audio_buffer = numpy.zeros(self.buffer_size,
                                           dtype=numpy.float32)

        self.ss_msg_buffer = queue.Queue()
        self.prev_event = None
        
        self._is_running = True

    def run(self):
        # User speech reception thread
        t1 = threading.Thread(target=self.us_listen_loop)
        # System speech reception thread
        t2 = threading.Thread(target=self.ss_listen_loop)
        # System speech buffering thread
        t3 = threading.Thread(target=self.ss_buffering_loop)
        # VAP execution thread
        t4 = threading.Thread(target=self.vap_loop)

        # Start threads
        t1.start()
        t2.start()
        t3.start()
        t4.start()

        t1.join()
        t2.join()
        t3.join()
        t4.join()
                
    def us_listen_loop(self):
        self.subscribe('ain', self.us_callback)

    def ss_listen_loop(self):
        self.subscribe('tts', self.ss_callback)

    def ss_buffering_loop(self):        
        delay_time = 0.0
        while True:
            start_time = time.time()
            try:
                # If system speech is in the queue, store it all in buffer
                chunk = self.ss_msg_buffer.get(block=False)
                self.ss_audio_buffer = self.shift_buffer(self.ss_audio_buffer, chunk)
            except:
                # If not, store silence for the combined delay and TTS frame length
                chunk_time = delay_time + self.tts_frame_length
                chunk_size = int(chunk_time * self.sample_rate)
                chunk = numpy.zeros(chunk_size)
                self.ss_audio_buffer = self.shift_buffer(self.ss_audio_buffer, chunk)
                delay_time = 0.0

            # Loop synchronized with TTS frame length
            proc_time = time.time() - start_time
            sleep_time = self.tts_frame_length - proc_time
            time.sleep(sleep_time)

            delay_time += proc_time
                    
    def vap_loop(self):
        encoder = EncoderCPC()
        transformer = TransformerStereo()
        model = VAP(encoder, transformer)
        ckpt = torch.load(self.model_name, map_location=device)['state_dict']
        restored_ckpt = {}
        for k,v in ckpt.items():
            restored_ckpt[k.replace('model.', '')] = v
        model.load_state_dict(restored_ckpt)
        model.eval()
        model.to(device)
        sys.stderr.write('Load VAP model: %s\n' % (self.model_name))
        sys.stderr.write('Device: %s\n' % (device))
        
        s_threshold = self.threshold
        u_threshold = 1 - self.threshold
        while True:
            # Combine data from both speakers to create a batch
            ss_audio = torch.Tensor(self.ss_audio_buffer)
            us_audio = torch.Tensor(self.us_audio_buffer)
            input_audio = torch.stack((ss_audio, us_audio))
            input_audio = input_audio.unsqueeze(0)
            batch = torch.Tensor(input_audio)
            batch = batch.to(device)

            frame_length = int(0.05 * self.sample_rate)
            u_pow = self.calc_pow(self.us_audio_buffer[-frame_length:])

            # Inference
            out = model.probs(batch)
            #print(out['vad'].shape,
            #      out['p_now'].shape,
            #      out['p_future'].shape,
            #      out['probs'].shape,
            #      out['H'].shape)

            # Get results
            p_ns = out['p_now'][0, :].cpu()
            p_fs = out['p_future'][0, :].cpu()
            vad_result = out['vad'][0, :].cpu()

            # Use the last frame's result for judgment
            score_n = p_ns[-1].item()
            score_f = p_fs[-1].item()
            score_v = vad_result[-1]

            # Determine event
            event = None
            if score_n >= s_threshold and score_f >= s_threshold:
                if self.prev_event != 'SYSTEM_BACKCHANNEL':
                    event = 'SYSTEM_TAKE_TURN'
            elif score_n >= s_threshold and score_f < u_threshold:
                if self.prev_event == 'USER_TAKE_TURN':
                    event = 'SYSTEM_BACKCHANNEL'
            elif score_n < u_threshold and score_f < u_threshold:
                event = 'USER_TAKE_TURN'


            # Issue messages
            # Scores for visualization
            score = {'p_now': score_n,
                     'p_future': score_f}
            snd_iu = self.createIU(score, 'score',
                                   RemdisUpdateType.ADD)
            self.publish(snd_iu, 'score')

            # Issue event only when there is a change
            if event and event != self.prev_event:
                snd_iu = self.createIU(event, 'vap',
                                       RemdisUpdateType.ADD)
                print('n:%.3f, f:%.3f, up: %.3f, %s' % (score_n,
                                                        score_f,
                                                        u_pow,
                                                        event))
                self.publish(snd_iu, 'vap')
                self.prev_event = event
            else:
                print('n:%.3f, f:%.3f, up: %.3f' % (score_n,
                                                    score_f,
                                                    u_pow))
            
    def us_callback(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        chunk = base64.b64decode(in_msg['body'].encode())
        chunk = numpy.frombuffer(chunk, dtype=numpy.int16)
        # Normalize amplitude to range between -1.0 and 1.0
        chunk = chunk.astype(numpy.float32) / 32768.0
        chunk = chunk.astype(numpy.float32)
        self.us_audio_buffer = self.shift_buffer(self.us_audio_buffer, chunk)

    def ss_callback(self, ch, method, properties, in_msg):
        in_msg = self.parse_msg(in_msg)
        chunk = base64.b64decode(in_msg['body'].encode())
        chunk = numpy.frombuffer(chunk, dtype=numpy.int16)
         # Normalize amplitude to range between -1.0 and 1.0
        chunk = chunk.astype(numpy.float32) / 32768.0
        if in_msg['update_type'] == RemdisUpdateType.ADD:
            self.ss_msg_buffer.put(chunk)

    # Function for storing in buffer
    def shift_buffer(self, in_buffer, chunk):
        chunk_size = len(chunk)
        in_buffer[:-chunk_size] = in_buffer[chunk_size:]
        in_buffer[-chunk_size:] = chunk
        return in_buffer

    # Debug audio saving function
    def save_wave(self, in_buffer, wav_filename='tmp.wav'):
        in_buffer = in_buffer.to('cpu').detach().numpy().copy()
        in_buffer = in_buffer * 32768
        in_buffer = in_buffer.astype(numpy.int16).T
        write(wav_filename, self.sample_rate, in_buffer)

    # Function to calculate the power of an audio waveform
    def calc_pow(self, segment):
        return numpy.log(numpy.mean(segment**2))

def main():
    vap = Audio_VAP()
    vap.run()

if __name__ == '__main__':
    main()