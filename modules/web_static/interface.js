// interface.js
document.addEventListener("DOMContentLoaded", function() {
    // Connect to the WebSocket server using socket.io
    const socket = io();

    const transcript = document.getElementById('transcript');
    const stateText = document.getElementById('state-text');
    const emotionDisplay = document.getElementById('emotion-display');
    const asrText = document.getElementById('asr-text');
    const statusBar = document.getElementById('status-bar');
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const connectionStatus = document.getElementById('connection-status');

    // Variable to hold the current (partial) user message displayed live
    let partialUserMessage = null;

    // Mapping of expressions to emoji for display purposes
    const expressionMap = {
        'normal': '😐',
        'thinking': '🤔',
        'speaking': '🗣️',
        'happy': '😊',
        'sad': '😢',
        'surprised': '😲',
        'angry': '😠',
        'confused': '😕',
        'listening': '👂',
        '0': '😐',
        '1': '😊',
        '2': '😲',
        '3': '🤔',
        '4': '🤔',
        '5': '😴',
        '6': '😒',
        '7': '😢',
        '8': '😳',
        '9': '😠'
    };

    // Mapping of action codes to human-readable status texts
    const actionMap = {
        'wait': 'waiting',
        'processing': 'thinking',
        'prepare_speech': 'preparing to speak',
        'start_speech': 'speaking',
        'stop_speech': 'finished speaking',
        'listening': 'listening',
        '0': 'waiting',
        '1': 'noticing',
        '2': 'nodding',
        '3': 'tilting head',
        '4': 'thinking',
        '5': 'bowing slightly',
        '6': 'bowing deeply',
        '7': 'waving',
        '8': 'waving energetically',
        '9': 'looking around'
    };

    // Socket event: Connected to the server
    socket.on('connect', () => {
        connectionStatus.textContent = 'Connected';
        connectionStatus.className = 'connected';
    });

    // Socket event: Disconnected from the server
    socket.on('disconnect', () => {
        connectionStatus.textContent = 'Disconnected';
        connectionStatus.className = 'disconnected';
    });

    // Permanent transcript updates (for both system and user finalized inputs)
    socket.on('new_text', function(data) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `message ${data.role}`;
        const roleLabel = document.createElement('div');
        roleLabel.className = 'role-label';
        roleLabel.textContent = data.role === 'system' ? 'System' : 'You';
        const textDiv = document.createElement('div');
        textDiv.textContent = data.text;
        messageDiv.appendChild(roleLabel);
        messageDiv.appendChild(textDiv);
        transcript.appendChild(messageDiv);
        transcript.scrollTop = transcript.scrollHeight;
    });

    // Event to display raw ASR tokens (live "Currently hearing:" area)
    socket.on('asr_token', function(data) {
        if (asrText.textContent) {
            asrText.textContent += " " + data.text;
        } else {
            asrText.textContent = data.text;
        }
        // Simple styling based on stability
        if (data.stability < 0.5) {
            asrText.classList.add('low-confidence');
        } else {
            asrText.classList.remove('low-confidence');
        }
    });

    // Event to update the live draft of the user's turn with partial ASR updates
    socket.on('partial_user', function(data) {
        if (!partialUserMessage) {
            partialUserMessage = document.createElement('div');
            partialUserMessage.className = 'message user partial';
            const roleLabel = document.createElement('div');
            roleLabel.className = 'role-label';
            roleLabel.textContent = 'You (partial)';
            partialUserMessage.appendChild(roleLabel);
            const textDiv = document.createElement('div');
            textDiv.className = 'user-partial-text';
            textDiv.textContent = data.text;
            partialUserMessage.appendChild(textDiv);
            transcript.appendChild(partialUserMessage);
        } else {
            let textDiv = partialUserMessage.querySelector('.user-partial-text');
            if (textDiv) {
                textDiv.textContent = data.text;
            }
        }
        transcript.scrollTop = transcript.scrollHeight;
    });

    // Event: User finished speaking—clear temporary displays
    socket.on('user_finished_speaking', function() {
        if (partialUserMessage) {
            partialUserMessage.remove();
            partialUserMessage = null;
        }
        asrText.textContent = '';
    });

    // Event: ASR input revoked—clear any temporary input displays
    socket.on('asr_revoked', function() {
        asrText.textContent = '';
        if (partialUserMessage) {
            partialUserMessage.remove();
            partialUserMessage = null;
        }
    });

    // Event: Update system expression/state (e.g., speaking, thinking)
    socket.on('system_state', function(data) {
        if (data.expression) {
            const expressionEmoji = expressionMap[data.expression] || '😐';
            emotionDisplay.textContent = expressionEmoji;
        }
        if (data.action) {
            const actionDesc = actionMap[data.action] || data.action;
            stateText.textContent = actionDesc;
            statusBar.className = '';
            if (actionDesc.includes('thinking') || data.action === 'processing') {
                statusBar.classList.add('thinking');
            } else if (actionDesc.includes('speaking') || data.action === 'start_speech') {
                statusBar.classList.add('speaking');
            }
        }
        if (data.progress !== null && data.progress !== undefined) {
            progressContainer.style.display = 'block';
            progressBar.style.width = `${data.progress * 100}%`;
        } else {
            progressContainer.style.display = 'none';
        }
    });

    // Event: System finished speaking—reset the state indicators
    socket.on('system_finished_speaking', function() {
        stateText.textContent = 'idle';
        statusBar.className = '';
        progressContainer.style.display = 'none';
    });
});
