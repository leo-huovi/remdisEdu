// interface.js
// Added microphone volume visualization, system speaking lockout, robot character implementation,
// and enlarged background display for hervanta.png with proper positioning

document.addEventListener("DOMContentLoaded", function() {
    // Define socket INSIDE DOMContentLoaded
    const socket = io();

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
    const backchannelBubble = document.getElementById('thought-bubble-backchannel');
    const backchannelContent = document.getElementById('thought-bubble-backchannel-content');
    const volumeMeter = document.getElementById('volume-meter'); // Volume meter elements
    const volumeBar = document.getElementById('volume-bar');
    const volumeMeterContainer = document.getElementById('volume-meter-container');
    const volumeMeterLabel = document.getElementById('volume-meter-label');

    // Use timeout value from global variable set in HTML
    const COMMIT_TIMEOUT_MS = (typeof COMMIT_TIMEOUT_MS_CONFIG !== 'undefined') ? COMMIT_TIMEOUT_MS_CONFIG : 3000;
    console.log(`Using commit timeout: ${COMMIT_TIMEOUT_MS}ms`);

    // Variables for ASR simulation
    let commitTimer = null; // Stores the timeout ID
    let lastSentText = ""; // Track last text successfully sent as partial
    const SEND_KEYS = [' ', '.', ',', '?', '!']; // Keys that trigger sending a partial update
    let lastMessageRole = null; // Track last message role for transcript clearing
    let systemIsSpeaking = false; // Track if system is speaking (for input lockout)
    let finalCommittedText = ""; // Track final committed text
    let backchannelHideTimer = null; // Timer for hiding backchannel bubble

    // Expression rate limiting
    let lastExpressionChange = 0;
    const EXPRESSION_CHANGE_DELAY = 1000; // 1 second minimum between expression changes

    // Mapping of expressions to robot image paths
    const robotMap = {
        'normal': 'robot1.png',
        'neutral': 'robot1.png',
        'thinking': 'robot2.png',
        'listening': 'robot2.png',
        'processing': 'robot2.png',
        'wait': 'robot2.png',
        'happy': 'robot3.png',
        'joy': 'robot3.png',
        'impressed': 'robot3.png',
        'surprised': 'robot3.png',
        'interested': 'robot3.png',
        'convinced': 'robot3.png',
        'sad': 'robot4.png',
        'compassion': 'robot4.png',
        'angry': 'robot4.png',
        'anger': 'robot4.png',
        'confused': 'robot4.png',
        'suspicion': 'robot4.png',
        'embarrassing': 'robot4.png',
        'nod': 'robot1.png',
        'sleepy': 'robot4.png',
        'speaking': 'robot1.png'
    };

    // Ensure emotion display uses image correctly positioned
    if (emotionDisplay) {
        // Create the img element if it doesn't exist
        if (!emotionDisplay.querySelector('img')) {
            const robotImg = document.createElement('img');
            robotImg.src = `static/character/robot1.png`; // Default robot
            robotImg.alt = "Robot character";
            emotionDisplay.appendChild(robotImg);
            // The bottom positioning is handled via CSS
        }
    }

    // Function to capitalize first letter of each sentence
    function capitalizeText(text) {
        if (!text) return text;

        // Split by sentence-ending punctuation followed by space
        const sentences = text.split(/([.!?]\s+)/g);

        for (let i = 0; i < sentences.length; i += 2) {
            if (sentences[i] && sentences[i].length > 0) {
                sentences[i] = sentences[i].charAt(0).toUpperCase() + sentences[i].slice(1);
            }
        }

        return sentences.join('');
    }

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
        if (data.role === 'system') {
            if (lastMessageRole !== 'system') {
                systemTranscript.textContent = ''; // Clear before adding
            }
            systemTranscript.textContent += (systemTranscript.textContent ? ' ' : '') + data.text;
            lastMessageRole = 'system'; // Update last role tracker

            // When system starts speaking, disable input
            systemIsSpeaking = true;
            document.body.classList.add('system-speaking');
            updateInputState();

            // DIRECT DOM MANIPULATION for muting
            if (volumeMeterLabel) {
                volumeMeterLabel.textContent = "NOT LISTENING 🔇";
                volumeMeterLabel.style.color = "#ff6b6b";
            }
            if (volumeMeterContainer) {
                volumeMeterContainer.classList.add('locked');
            }
            if (volumeBar) {
                volumeBar.className = 'volume-muted';
                volumeBar.style.height = '10%';
            }
        } else if (data.role === 'user') {
            // Apply capitalization to user transcript
            let displayText = capitalizeText(data.text);
            userTranscript.textContent = displayText; // Replace final user text
            lastMessageRole = 'user'; // Update last role tracker
            if (thoughtBubble) thoughtBubble.classList.remove('active');
            if (centralConceptBubble) centralConceptBubble.classList.remove('active');
            if (backchannelBubble) backchannelBubble.classList.remove('active');
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
        if (backchannelBubble) backchannelBubble.classList.remove('active');
    });

    // Listen for VAP events to get the actual final text and ASR lockout state
    socket.on('vap_event', function(data) {
        console.log("Received VAP event:", data.event);

        if (data.event === 'ASR_COMMIT' && data.text) {
            console.log("Received VAP ASR_COMMIT with final text:", data.text);
            finalCommittedText = data.text;
            // Update user transcript with properly capitalized final text
            let displayText = capitalizeText(finalCommittedText);
            userTranscript.textContent = displayText;
        }
        else if (data.event === 'START_SPEECH') {
            console.log("System starting speech - FORCING MUTE");
            systemIsSpeaking = true;
            document.body.classList.add('system-speaking');

            // DIRECT DOM MANIPULATION
            if (volumeMeterLabel) {
                volumeMeterLabel.textContent = "NOT LISTENING 🔇";
                volumeMeterLabel.style.color = "#ff6b6b";
            }
            if (volumeMeterContainer) {
                volumeMeterContainer.classList.add('locked');
            }
            if (volumeBar) {
                volumeBar.className = 'volume-muted';
                volumeBar.style.height = '10%';
            }

            // Update input state
            updateInputState();
        }
        else if (data.event === 'TTS_COMMIT') {
            console.log("System finished speech - UNMUTING");
            systemIsSpeaking = false;
            document.body.classList.remove('system-speaking');

            // DIRECT DOM MANIPULATION
            if (volumeMeterLabel) {
                volumeMeterLabel.textContent = "LISTENING";
                volumeMeterLabel.style.color = "#ccc0e8";
            }
            if (volumeMeterContainer) {
                volumeMeterContainer.classList.remove('locked');
            }

            // Update input state
            updateInputState();
        }
        else if (data.event === 'ASR_LOCKOUT') {
            // Direct ASR lockout state control
            const isLocked = data.is_locked === true;
            console.log(`Received ASR_LOCKOUT - locked: ${isLocked}`);
            systemIsSpeaking = isLocked; // Ensure consistent state

            // DIRECT DOM MANIPULATION based on lockout state
            if (isLocked) {
                if (volumeMeterLabel) {
                    volumeMeterLabel.textContent = "NOT LISTENING 🔇";
                    volumeMeterLabel.style.color = "#ff6b6b";
                }
                if (volumeMeterContainer) {
                    volumeMeterContainer.classList.add('locked');
                }
                if (volumeBar) {
                    volumeBar.className = 'volume-muted';
                    volumeBar.style.height = '10%';
                }
            } else {
                if (volumeMeterLabel) {
                    volumeMeterLabel.textContent = "LISTENING";
                    volumeMeterLabel.style.color = "#ccc0e8";
                }
                if (volumeMeterContainer) {
                    volumeMeterContainer.classList.remove('locked');
                }
            }

            updateInputState();
        }
    });

    // Handle system state updates
    socket.on('system_state', function(data) {
        if (!data) { console.error("Received empty system_state data!"); return; }

        // Update Robot Character and Action
        const expression = data.expression ? data.expression.toLowerCase() : 'normal';
        const action = data.action ? data.action.toLowerCase() : 'idle';

        // Rate limit expression changes
        const now = Date.now();
        if (now - lastExpressionChange >= EXPRESSION_CHANGE_DELAY) {
            // Update robot image based on expression
            const robotImage = robotMap[expression] || 'robot1.png';
            if (emotionDisplay) {
                const robotImg = emotionDisplay.querySelector('img');
                if (robotImg) {
                    robotImg.src = `static/character/${robotImage}`;
                }

                // Reset body classes for animations
                document.body.className = '';
                document.body.classList.add(`expression-${expression}`);

                // Add action classes for animations - but don't override speaking state
                if (action) {
                    if ((action === 'thinking' || action === 'processing') && !systemIsSpeaking) {
                        document.body.classList.add('system-thinking');
                    }
                    else if (action === 'speaking' || systemIsSpeaking) {
                        document.body.classList.add('system-speaking');
                    }
                }
            }

            // Update last expression change timestamp
            lastExpressionChange = now;
        }

        // Update Action Text
        if(stateText) { stateText.textContent = action; }

        // Update First Thought Bubble (Current Text)
        const currentText = data.current_text || "";
        if (thoughtContent && thoughtBubble) {
            if (currentText.trim() !== "") {
                 // Apply capitalization to current text in thought bubble
                 const displayText = capitalizeText(currentText);
                 thoughtContent.textContent = displayText;
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

        // Handle Backchannel Bubble (above robot)
        const backchannelActions = ['interested', 'agreed', 'disagree', 'confused', 'thinking',
                                  'nod', 'laugh', 'confirm', 'unsure', 'hmm', 'aha', 'oh', 'wow'];

        // Show backchannel bubble for relevant actions
        if (backchannelBubble && backchannelContent) {
            if (action && backchannelActions.includes(action)) {
                // Filter out "wait" which is the default value
                    // Get display text directly from action
                    const displayText = action;
                    backchannelContent.textContent = displayText;
                    backchannelBubble.classList.add('active');

                    // Auto-hide backchannel after a delay
                    if (backchannelHideTimer) clearTimeout(backchannelHideTimer);
                    backchannelHideTimer = setTimeout(() => {
                        backchannelBubble.classList.remove('active');
                    }, 3000); // Hide after 3 seconds
            } else {
                // Only hide immediately for certain actions
                const immediateHideActions = ['idle', 'normal', 'speaking'];
                if (immediateHideActions.includes(action)) {
                    backchannelBubble.classList.remove('active');
                    if (backchannelHideTimer) clearTimeout(backchannelHideTimer);
                }
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

    // Handle microphone volume level updates
    socket.on('mic_level', function(data) {
        // Update volume meter if it exists
        if (volumeBar && volumeMeterContainer) {
            // For vertical meter, set height instead of width
            const scaledLevel = Math.min(100, data.level * 2);

            // If system is speaking, force muted appearance
            if (systemIsSpeaking) {
                // Force label to show NOT LISTENING
                if (volumeMeterLabel) {
                    volumeMeterLabel.textContent = "🔇　NOT LISTENING 🔇";
                    volumeMeterLabel.style.color = "#ff6b6b"; // Red color
                }

                // Use muted/disabled appearance
                volumeBar.className = 'volume-muted';
                volumeBar.style.height = '10%'; // Minimal height when muted
                volumeMeterContainer.classList.add('locked');
            } else {
                // Update normally when not speaking
                volumeBar.style.height = scaledLevel + '%';

                // Color coding based on volume level
                if (data.level < 10) {
                    volumeBar.className = 'volume-low';
                } else if (data.level < 30) {
                    volumeBar.className = 'volume-medium';
                } else {
                    volumeBar.className = 'volume-high';
                }

                // Reset to LISTENING
                if (volumeMeterLabel) {
                    volumeMeterLabel.textContent = "LISTENING";
                    volumeMeterLabel.style.color = "#ccc0e8"; // Reset color
                }
                volumeMeterContainer.classList.remove('locked');
            }
        }
    });

    // Function to update the input state based on system speaking status
    function updateInputState() {
        if (asrInput) {
            if (systemIsSpeaking) {
                asrInput.disabled = true;
                asrInput.placeholder = "System is speaking...";
                asrInput.classList.add('disabled');
            } else {
                asrInput.disabled = false;
                asrInput.placeholder = "Type your message here...";
                asrInput.classList.remove('disabled');
                asrInput.focus(); // Auto-focus when enabled
            }
        }
    }

    socket.on('system_finished_speaking', function() {
        console.log("Backend signaled system finished speaking turn");
        // Note: We'll keep systemIsSpeaking=true until we get TTS_COMMIT
        lastMessageRole = null; // Reset role tracker
        if(stateText) stateText.textContent = 'idle';
        // Don't reset robot here - let the rate limiter handle it
        if(progressContainer) progressContainer.style.display = 'none';
    });

    socket.on('input_blocked', function(data) {
        // Visual feedback when input is blocked
        if (asrInput) {
            asrInput.classList.add('input-blocked');
            // Remove the class after a short delay for visual feedback
            setTimeout(() => {
                asrInput.classList.remove('input-blocked');
            }, 500);
        }
    });

    // --- ASR Input Simulation Logic ---

    // 1. Detect user activity (keydown and input) to reset the commit timer
    asrInput.addEventListener('keydown', function(e) {
        // Skip if system is speaking
        if (systemIsSpeaking) return;

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
                 if (textToSend.trim() && textToSend !== lastSentText) {
                     sendUserInput(textToSend, false); // Send the full value including the trigger key
                 }
            }, 0);
        }
    });

    asrInput.addEventListener('input', function(e) {
        // Skip if system is speaking
        if (systemIsSpeaking) return;

        // This event fires AFTER keydown and the input value has changed
        updateTypingStatus('typing'); // Show user is typing
        resetCommitTimer(); // Reset timer on ANY input change (typing, pasting, deleting)
    });

    // 2. Function to reset or start the inactivity timer for auto-commit
    function resetCommitTimer() {
        if (commitTimer) clearTimeout(commitTimer); // Clear existing timer
        // Start a new timer using the timeout value from backend/config
        commitTimer = setTimeout(() => {
            // Skip if system is speaking
            if (systemIsSpeaking) return;

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
        // Skip if system is speaking
        if (systemIsSpeaking) return;

        // Prevent sending empty strings for partials
        if (!isFinal && !text.trim()) {
             return;
        }
        // Prevent sending duplicate partials
        if (!isFinal && text === lastSentText) {
             return;
        }

        // Trim final commits, but send partials exactly as they are (including trailing space)
        const textPayload = isFinal ? text.trim() : text;

        socket.emit('user_input', { text: textPayload, is_final: isFinal });

        // Update tracking variable *after* successful send attempt for partials
        if (!isFinal) {
             lastSentText = textPayload;
        } else {
            // For final commits, save the text but let the VAP event update the display
            finalCommittedText = textPayload;
            // Show immediate feedback with capitalization
            if (userTranscript) {
                userTranscript.textContent = capitalizeText(textPayload);
            }
        }

        // --- UI Updates ---
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
    updateInputState(); // Set initial input state
    if(asrInput) asrInput.focus();

}); // End DOMContentLoaded
