import sys
import json
import time
import re
import string

import openai

from base import MMDAgentEXLabel


class ResponseGenerator:
    def __init__(self, config, asr_timestamp, query, dialogue_history, prompts, client):
        # Load settings
        self.max_tokens = config['ChatGPT']['max_tokens']
        self.max_message_num_in_context = config['ChatGPT']['max_message_num_in_context']
        self.model = config['ChatGPT']['response_generation_model']

        # Information about the user's speech to be processed
        self.asr_timestamp = asr_timestamp
        self.query = query
        self.dialogue_history = dialogue_history
        self.prompts = prompts

        # Variable to hold and parse the response being generated
        self.response_fragment = ''
        raw_split_pattern = config['ChatGPT']['split_pattern']
        self.split_pattern = re.compile(raw_split_pattern)

        # Dialogue context to input into ChatGPT
        messages = []

        # Add past dialogue history to dialogue context
        i = max(0, len(self.dialogue_history) - self.max_message_num_in_context)
        messages.extend(self.dialogue_history[i:])

        # Add prompt and new user speech to dialogue context
        if query:
            messages.extend([
                {'role': 'user', 'content': self.prompts['RESP']},
                {'role': 'system', 'content': "OK"},
                {'role': 'user', 'content': query}
            ])
        # Add prompt for self-spoken speech when there is no new user speech
        else:
            messages.extend([
                {'role': 'user', 'content': prompts['TO']}
            ])

        self.log(f"Call ChatGPT: {query=}")

        # Input dialogue context into ChatGPT and start generating responses in a streaming format
        self.response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            max_tokens=self.max_tokens,
            stream=True
        )
    
    # Called in the send_response function of Dialogue, sequentially returns fragments of responses
    def __next__(self):
        # Parse arguments (e.g., '1_joy|6_nod') to obtain expression and action
        def _parse_split(split):
            expression = MMDAgentEXLabel.id2expression[0]
            action = MMDAgentEXLabel.id2action[0]

            # Obtain expression/action
            if "|" in split:
                expression, action = split.split("|", 1)

                expression = expression.split("_")[0]
                expression = int(expression) if expression.isdigit() else 0
                expression = MMDAgentEXLabel.id2expression[expression]

                action = action.split("_")[0]
                action = int(action) if action.isdigit() else 0
                action = MMDAgentEXLabel.id2action[action]

            return {
                "expression": expression,
                "action": action
            }

        # Sequentially parse and return ChatGPT's response
        for chunk in self.response:
            chunk_message = chunk.choices[0].delta

            if hasattr(chunk_message, 'content'):
                new_token = chunk_message.content

                if not new_token:
                    continue
                
                # Add fragments of the response
                if new_token != "/":
                    self.response_fragment += new_token

                # Split the response by punctuation
                splits = self.split_pattern.split(self.response_fragment, 1)

                # Retain remaining fragment for the next loop
                self.response_fragment = splits[-1]

                # Return the first fragment if punctuation existed
                if len(splits) == 2 or new_token == "/":
                    if splits[0]:
                        return {"phrase": splits[0]}
                
                # Return remaining fragment if the end of the response has come
                if new_token == "/":
                    if self.response_fragment:
                        return {"phrase": self.response_fragment}
                    self.response_fragment = ''
            else:
                # Parse and return the remaining fragment if ChatGPT's response is complete
                if self.response_fragment:
                    return _parse_split(self.response_fragment)

        raise StopIteration
    
    # Make ResponseGenerator an iterator
    def __iter__(self):
        return self

    # Debug log output
    def log(self, *args, **kwargs):
        print(f"[{time.time():.5f}]", *args, flush=True, **kwargs)


class ResponseChatGPT():
    def __init__(self, config, prompts):
        self.config = config
        self.prompts = prompts

        # Load settings
        self.client = openai.OpenAI(api_key=config['ChatGPT']['api_key'])

        # Variables to retain information about the user's input speech
        self.user_utterance = ''
        self.response = ''
        self.last_asr_iu_id = ''
        self.asr_time = 0.0
    
    # Start the call to ChatGPT
    def run(self, asr_timestamp, user_utterance, dialogue_history, last_asr_iu_id, parent_llm_buffer):
        self.user_utterance = user_utterance
        self.last_asr_iu_id = last_asr_iu_id
        self.asr_time = asr_timestamp

        # Call ChatGPT and start generating responses
        self.response = ResponseGenerator(self.config, asr_timestamp, user_utterance, dialogue_history, self.prompts, self.client)

        # Add itself to the LLM buffer held by the Dialogue module
        parent_llm_buffer.put(self)


if __name__ == "__main__":
    client = openai.OpenAI(api_key='<enter your API key>')

    config = {'ChatGPT': {
        'max_tokens': 64,
        'max_message_num_in_context': 3,
        'response_generation_model': 'gpt-4o-mini'
    }}

    asr_timestamp = time.time()
    query = 'Today is fine day.'
    dialogue_history = []
    prompts = {}

    with open('./prompt/response.txt', encoding='utf-8') as f:
        prompts['RESP'] = f.read()

    response_generator = ResponseGenerator(config, asr_timestamp, query, dialogue_history, prompts, client)

    for part in response_generator:
        response_generator.log(part)
