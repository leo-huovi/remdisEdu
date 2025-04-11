// interface.js
// Simulates ASR behavior: Sends partial updates on space/punctuation.
// Commits after a period of inactivity defined by the backend config.

document.addEventListener("DOMContentLoaded", function() {
    // --- Define socket INSIDE DOMContentLoaded ---
    const socket = io();
    // ------------------------------------------

    // DOM elements
    const emotionDisplay = document.getElementById('emotion-display');
    const stateText = document.getElementById('state-text');
    const progressContainer = document.getElementById('progress-container');
    const progressBar = document.getElementById('progress-bar');
    const connectionStatus = document.getElementById('connection-status');
    const systemTranscript = document.getElementById('system-transcript');
    const userTranscript = document.getElementById('user-transcript');
    const asrInput = document.getElementById('asr-input');
    const statusIndicator = document.getElementById('status-indicator');
    const typingStatus = document.getElementById('typing-status');
    const thoughtBubble = document.getElementById('thought-bubble');
    const thoughtContent = document.getElementById('thought-bubble-content');
    const centralConceptContent = document.getElementById('thought-bubble-central-content');
    const centralConceptBubble = document.getElementById('thought-bubble-central');

    // --- Use timeout value from global variable set in HTML ---
    // Use || 3000 as a fallback if the variable wasn't set correctly in index.html
    const COMMIT_TIMEOUT_MS = (typeof COMMIT_TIMEOUT_MS_CONFIG !== 'undefined') ? COMMIT_TIMEOUT_MS_CONFIG : 3000;
    console.log(`Using commit timeout: ${COMMIT_TIMEOUT_MS}ms`); // Log the value being used
    // ---------------------------------------------------------

    // Variables for ASR simulation
    let commitTimer = null; // Stores the timeout ID
    let lastSentText = ""; // Track last text successfully sent as partial
    const SEND_KEYS = [' ', '.', ',', '?', '!']; // Keys that trigger sending a partial update
    let lastMessageRole = null; // Track last message role for transcript clearing

    // Mapping of expressions to emoji
    const expressionMap = {
        'normal': '😐', 'thinking': '🤔', 'speaking': '🗣️', 'happy': '😊',
        'joy': '😊', 'sad': '😢', 'compassion': '😢', 'surprised': '😲',
        'impressed': '😲', 'angry': '😠', 'anger': '😠', 'confused': '😕',
        'listening': '👂', 'convinced': '🧐', 'interested': '👀', 'sleepy': '😴',
        'suspicion': '🤨', 'embarrassing': '😳', 'wait': '⏳', 'nod': '🙂',
        'processing': '⚙️'
        // Add others as needed
    };

    // --- Socket Event Handlers ---
    socket.on('connect', () => {
        connectionStatus.textContent = 'Connected';
        connectionStatus.className = 'connected';
        console.log("Socket connected");
    });

    socket.on('disconnect', () => {
        connectionStatus.textContent = 'Disconnected';
        connectionStatus.className = 'disconnected';
        console.log("Socket disconnected");
    });

    socket.on('new_text', function(data) {
        // console.log("Received new_text (final transcript update):", data); // Less verbose
        if (data.role === 'system') {
            if (lastMessageRole !== 'system') {
                // console.log("Clearing system transcript for new turn.");
                systemTranscript.textContent = ''; // Clear before adding
            }
            systemTranscript.textContent += (systemTranscript.textContent ? ' ' : '') + data.text;
            lastMessageRole = 'system'; // Update last role tracker
            // console.log("Updated system transcript:", systemTranscript.textContent);
        } else if (data.role === 'user') {
            userTranscript.textContent = data.text; // Replace final user text
            lastMessageRole = 'user'; // Update last role tracker
            // console.log("Updated user transcript:", data.text);
            if (thoughtBubble) thoughtBubble.classList.remove('active');
            if (centralConceptBubble) centralConceptBubble.classList.remove('active');
        }
    });

    socket.on('asr_token', function(data) { /* Minimal action likely */ });

    socket.on('user_finished_speaking', function() {
        // console.log("Backend signaled user finished speaking (commit processed)");
    });

    socket.on('asr_revoked', function() {
        console.log("Backend signaled ASR revoked");
        if (thoughtBubble) thoughtBubble.classList.remove('active');
        if (thoughtContent) thoughtContent.textContent = '';
        if (centralConceptBubble) centralConceptBubble.classList.remove('active');
        if (centralConceptContent) centralConceptContent.textContent = '';
    });

    socket.on('system_state', function(data) {
        // console.log("Received system_state update:", data); // Verbose log
        if (!data) { console.error("Received empty system_state data!"); return; }

        // Update Emoji
        const expression = data.expression ? data.expression.toLowerCase() : 'normal';
        const expressionEmoji = expressionMap[expression] || '😐';
        if (emotionDisplay) {
             emotionDisplay.textContent = expressionEmoji;
             document.body.className = ''; // Reset classes
             document.body.classList.add(`expression-${expression}`);
             if(data.action) { /* Add action classes if defined in CSS */
                 if (data.action === 'thinking' || data.action === 'processing') { document.body.classList.add('system-thinking'); }
                 else if (data.action === 'speaking') { document.body.classList.add('system-speaking'); }
             }
             if ((data.action === 'thinking' || data.action === 'processing') && expressionEmoji === '😐') {
                 emotionDisplay.textContent = expressionMap['thinking'] || '🤔';
             }
        }

        // Update Action Text
        const action = data.action || 'idle';
        if(stateText) { stateText.textContent = action; }

        // Update First Thought Bubble (Current Text)
        const currentText = data.current_text || "";
        if (thoughtContent && thoughtBubble) {
            if (currentText.trim() !== "") {
                 thoughtContent.textContent = currentText; // Use variable 'currentText'
                 thoughtBubble.classList.add('active');
            } else {
                 thoughtBubble.classList.remove('active');
                 thoughtContent.textContent = '';
            }
        }

        // Update Second Thought Bubble (Concept)
        const concept = data.concept ? data.concept.trim() : "";
        const nonMeaningfulConcepts = ["", "...", "unknown topic", null, undefined];
        if (centralConceptContent && centralConceptBubble) {
            if (concept && !nonMeaningfulConcepts.includes(concept)) {
                centralConceptContent.textContent = concept;
                centralConceptBubble.classList.add('active');
            } else {
                centralConceptBubble.classList.remove('active');
                centralConceptContent.textContent = "";
            }
        }

        // Update Progress Bar
        const progressBar = document.getElementById('progress-bar'); // Ensure refetch if needed
        if (data.progress !== undefined && data.progress !== null) {
            if(progressContainer && progressBar) {
                progressContainer.style.display = 'block';
                progressBar.style.width = data.progress + '%';
            }
        } else {
           if(progressContainer) progressContainer.style.display = 'none';
           if(progressBar) progressBar.style.width = '0%';
        }
    });

    socket.on('system_finished_speaking', function() {
        console.log("Backend signaled system finished speaking turn");
        lastMessageRole = null; // Reset role tracker
        if(stateText) stateText.textContent = 'idle';
        if (emotionDisplay && emotionDisplay.textContent !== '😐') { emotionDisplay.textContent = '😐'; document.body.className = ''; }
        else if (!emotionDisplay) { document.body.className = ''; }
        if(progressContainer) progressContainer.style.display = 'none';
    });

    // --- ASR Input Simulation Logic ---

    // 1. Detect user activity (keydown and input) to reset the commit timer
    asrInput.addEventListener('keydown', function(e) {
        resetCommitTimer(); // Reset timer on ANY key press down

        if (e.key === 'Enter') {
            e.preventDefault(); // Prevent form submission/newline
            const currentText = this.value.trim();
            console.log(`Enter detected. Committing: "${currentText}"`);
            sendUserInput(currentText, true); // Send FINAL on Enter
            clearInputAndReset();
        } else if (SEND_KEYS.includes(e.key)) {
             // Send partial update *after* the character is added by the browser
            setTimeout(() => {
                 const textToSend = this.value; // Get text *after* space/punct is added
                 // console.log(`[Debug] Trigger '${e.key}'. Text in input after timeout: "${textToSend}"`);
                 if (textToSend.trim() && textToSend !== lastSentText) {
                     // console.log(`Send trigger key ('${e.key}') detected. Sending partial: "${textToSend}"`);
                     sendUserInput(textToSend, false); // Send the full value including the trigger key
                 }
                 // else { console.log(`[Debug] Skipping send for trigger '${e.key}'.`); }
            }, 0);
        }
        // No need to send anything else on other keydowns
    });

    asrInput.addEventListener('input', function(e) {
        // This event fires AFTER keydown and the input value has changed
        updateTypingStatus('typing'); // Show user is typing
        resetCommitTimer(); // Reset timer on ANY input change (typing, pasting, deleting)
    });

    // 2. Function to reset or start the inactivity timer for auto-commit
    function resetCommitTimer() {
        if (commitTimer) clearTimeout(commitTimer); // Clear existing timer
        // Start a new timer using the timeout value from backend/config
        commitTimer = setTimeout(() => {
            const textToCommit = asrInput.value.trim(); // Get text at the moment timer fires
            // Only commit if there's actually text in the input box
            if (textToCommit !== '') {
                console.log(`Commit timer fired (${COMMIT_TIMEOUT_MS}ms inactivity). Committing: "${textToCommit}"`);
                sendUserInput(textToCommit, true); // Send FINAL commit
                clearInputAndReset(); // Clear input after committing
            } else {
                // If the timer fires and the input is empty, just ensure status is idle
                updateTypingStatus('idle');
            }
            commitTimer = null; // Clear timer variable
        }, COMMIT_TIMEOUT_MS); // Use the variable holding the timeout duration
    }

    // 3. Function to actually send data via SocketIO
    function sendUserInput(text, isFinal) {
        // Prevent sending empty strings for partials
        if (!isFinal && !text.trim()) {
             // console.log("Skipping send: Empty partial text.");
             return;
        }
        // Prevent sending duplicate partials
        if (!isFinal && text === lastSentText) {
             // console.log(`Skipping send: Text "${text}" identical to last sent partial "${lastSentText}".`);
             return;
        }

        // Trim final commits, but send partials exactly as they are (including trailing space)
        const textPayload = isFinal ? text.trim() : text;

        // console.log(`>>> Emitting 'user_input': { text: "${textPayload}", is_final: ${isFinal} }`);
        socket.emit('user_input', { text: textPayload, is_final: isFinal });

        // Update tracking variable *after* successful send attempt for partials
        if (!isFinal) {
             lastSentText = textPayload;
        }

        // --- UI Updates ---
        // Update user transcript immediately on FINAL for perceived speed
        if (isFinal && userTranscript) {
             userTranscript.textContent = textPayload;
        }
        // Update status indicator
        if (isFinal) {
            updateTypingStatus('sent');
             // Schedule return to idle after a short delay
             setTimeout(() => {
                  // Check if user hasn't started typing again immediately
                  if (asrInput.value === '') {
                       updateTypingStatus('idle');
                  }
             }, 1000); // Return to idle 1 sec after sent
        } else {
            updateTypingStatus('typing'); // Stay typing after sending partial
        }
    }

    // 4. Helper to clear input and reset state
    function clearInputAndReset() {
        if (commitTimer) clearTimeout(commitTimer); // Clear timer when explicitly committing/clearing
        commitTimer = null;
        asrInput.value = ''; // Clear text box
        lastSentText = ""; // Reset last sent text
        // Don't immediately go to idle here, wait for 'sent' status timeout or next input
    }

    // 5. Function to update typing status indicator UI
    function updateTypingStatus(status) {
        if (!statusIndicator || !typingStatus) return;
        statusIndicator.className = ''; // Clear previous classes
        statusIndicator.classList.add(`status-${status}`);
        let statusText = status.charAt(0).toUpperCase() + status.slice(1);
        if (status === 'typing') statusText += '...';
        if (status === 'sent') statusText += '!';
        typingStatus.textContent = statusText;
    }

    // Initialize
    updateTypingStatus('idle');
    if(asrInput) asrInput.focus();

}); // End DOMContentLoaded
