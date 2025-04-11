// interface.js
document.addEventListener("DOMContentLoaded", function() {
    // Connect to the WebSocket server using socket.io
    const socket = io();

    // DOM elements
    const emotionDisplay = document.getElementById('emotion-display');
    const stateText = document.getElementById('state-text');
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const connectionStatus = document.getElementById('connection-status');
    const systemTranscript = document.getElementById('system-transcript');
    const userTranscript = document.getElementById('user-transcript');
    const asrText = document.getElementById('asr-text');
    const asrInput = document.getElementById('asr-input');
    const statusIndicator = document.getElementById('status-indicator');
    const typingStatus = document.getElementById('typing-status');

    // Variables for ASR simulation
    let isTyping = false;
    let typingTimer = null;
    let partialTimer = null;
    let lastSentText = "";

    // Mapping of expressions to emoji
    const expressionMap = {
        'normal': '😐',
        'thinking': '🤔',
        'speaking': '🗣️',
        'happy': '😊',
        'joy': '😊',
        'sad': '😢',
        'compassion': '😢',
        'surprised': '😲',
        'impressed': '😲',
        'angry': '😠',
        'anger': '😠',
        'confused': '😕',
        'listening': '👂',
        'convinced': '🧐',
        'sleepy': '😴',
        'suspicion': '🤨',
        'embarrassing': '😳'
    };

    // Socket connection events
    socket.on('connect', () => {
        connectionStatus.textContent = 'Connected';
        connectionStatus.className = 'connected';
    });

    socket.on('disconnect', () => {
        connectionStatus.textContent = 'Disconnected';
        connectionStatus.className = 'disconnected';
    });

    // Update system transcript (limited to 2 visible lines)
    socket.on('new_text', function(data) {
        if (data.role === 'system') {
            // Update system transcript
            systemTranscript.textContent = data.text;
        } else if (data.role === 'user') {
            // Update user transcript
            userTranscript.textContent = data.text;
        }
    });

    // Update ASR display
    socket.on('asr_token', function(data) {
        if (asrText.textContent) {
            asrText.textContent += " " + data.text;
        } else {
            asrText.textContent = data.text;
        }

        // Apply styling based on confidence
        if (data.stability < 0.5) {
            asrText.classList.add('low-confidence');
        } else {
            asrText.classList.remove('low-confidence');
        }
    });

    // Clear ASR display when user finishes speaking
    socket.on('user_finished_speaking', function() {
        asrText.textContent = '';
    });

    // Clear ASR when revoked
    socket.on('asr_revoked', function() {
        asrText.textContent = '';
    });

    // Update agent state
    socket.on('system_state', function(data) {
        // Update expression emoji
        if (data.expression) {
            const expressionEmoji = expressionMap[data.expression] || '😐';
            emotionDisplay.textContent = expressionEmoji;

            // Update class for styling
            document.body.className = '';
            document.body.classList.add(`expression-${data.expression}`);
        }

        // Update state text
        if (data.action) {
            stateText.textContent = data.action;

            // Update animation state
            document.body.className = document.body.className || '';
            if (data.action === 'thinking' || data.action === 'processing') {
                document.body.classList.add('system-thinking');
            } else if (data.action === 'speaking' || data.action === 'start_speech') {
                document.body.classList.add('system-speaking');
            }
        }

        // Update progress bar if provided
        if (data.progress !== null && data.progress !== undefined) {
            progressContainer.style.display = 'block';
            progressBar.style.width = `${data.progress * 100}%`;
        } else {
            progressContainer.style.display = 'none';
        }
    });

    // Reset system state when finished speaking
    socket.on('system_finished_speaking', function() {
        stateText.textContent = 'idle';
        document.body.className = '';
        progressContainer.style.display = 'none';
    });

    // ASR input simulation
    asrInput.addEventListener('input', function() {
        const currentText = this.value;

        // Update typing status
        isTyping = true;
        updateTypingStatus('typing');

        // Clear previous timers
        if (typingTimer) clearTimeout(typingTimer);
        if (partialTimer) clearTimeout(partialTimer);

        // Set timer to detect when typing stops (3 seconds)
        typingTimer = setTimeout(function() {
            if (currentText.trim() !== '') {
                // Send final input
                sendUserInput(currentText, true);
                asrInput.value = '';
                updateTypingStatus('sent');

                // Reset after a moment
                setTimeout(() => {
                    updateTypingStatus('idle');
                }, 1500);
            } else {
                updateTypingStatus('idle');
            }
            isTyping = false;
        }, 3000);

        // Send partial updates every 0.5 seconds while typing
        partialTimer = setTimeout(function() {
            if (currentText !== lastSentText) {
                sendUserInput(currentText, false);
                lastSentText = currentText;
            }
        }, 500);
    });

    // Handle Enter key for immediate send
    asrInput.addEventListener('keydown', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();

            const currentText = this.value.trim();
            if (currentText !== '') {
                // Clear any pending timers
                if (typingTimer) clearTimeout(typingTimer);
                if (partialTimer) clearTimeout(partialTimer);

                // Send as final input
                sendUserInput(currentText, true);
                this.value = '';
                updateTypingStatus('sent');

                // Reset after a moment
                setTimeout(() => {
                    updateTypingStatus('idle');
                }, 1500);
            }
            isTyping = false;
        }
    });

    // Function to send user input to server
    function sendUserInput(text, isFinal) {
        socket.emit('user_input', {
            text: text,
            is_final: isFinal
        });

        // Update the ASR display for immediate feedback
        asrText.textContent = text;
        asrText.classList.remove('low-confidence');
    }

    // Function to update typing status indicator
    function updateTypingStatus(status) {
        statusIndicator.className = '';
        statusIndicator.classList.add(`status-${status}`);

        switch(status) {
            case 'idle':
                typingStatus.textContent = 'Idle';
                break;
            case 'typing':
                typingStatus.textContent = 'Typing...';
                break;
            case 'sent':
                typingStatus.textContent = 'Sent!';
                break;
            default:
                typingStatus.textContent = status;
        }
    }

    // Initialize input status
    updateTypingStatus('idle');

    // Focus the input field on page load
    asrInput.focus();
});
