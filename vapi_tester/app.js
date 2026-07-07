import Vapi from 'https://esm.sh/@vapi-ai/web@2.1.0';

// DOM Elements
const publicKeyInput = document.getElementById('publicKey');
const assistantIdInput = document.getElementById('assistantId');
const saveConfigBtn = document.getElementById('saveConfigBtn');
const configSuccess = document.getElementById('configSuccess');
const micBtn = document.getElementById('micBtn');
const micContainer = document.querySelector('.mic-container');
const callStateText = document.getElementById('callStateText');
const transcriptBox = document.getElementById('transcriptBox');
const transcriptPlaceholder = document.getElementById('transcriptPlaceholder');
const statusIndicator = document.getElementById('statusIndicator');
const statusText = statusIndicator.querySelector('.text');

// State
let vapi = null;
let isCallActive = false;

// Load saved config
const savedPubKey = localStorage.getItem('vapiPubKey') || '9e62ba06-8c38-4da4-8474-565764c2fd31';
const savedAsstId = localStorage.getItem('vapiAsstId') || '7d1a9d93-81fc-45b5-a24d-b407ae77f056';
if (savedPubKey) publicKeyInput.value = savedPubKey;
if (savedAsstId) assistantIdInput.value = savedAsstId;

if (savedPubKey && savedAsstId) {
    initializeVapi(savedPubKey);
}

// Event Listeners
saveConfigBtn.addEventListener('click', () => {
    const pubKey = publicKeyInput.value.trim();
    const asstId = assistantIdInput.value.trim();
    
    if (!pubKey || !asstId) {
        alert("Please enter both Public Key and Assistant ID.");
        return;
    }

    localStorage.setItem('vapiPubKey', pubKey);
    localStorage.setItem('vapiAsstId', asstId);

    configSuccess.classList.remove('hidden');
    setTimeout(() => configSuccess.classList.add('hidden'), 3000);

    initializeVapi(pubKey);
});

micBtn.addEventListener('click', toggleCall);

function initializeVapi(publicKey) {
    try {
        if (vapi) {
            vapi.stop();
        }
        
        vapi = new Vapi(publicKey);
        micBtn.disabled = false;
        setStatus('connected', 'Ready to Call');
        
        // Setup Vapi Event Listeners
        vapi.on('call-start', () => {
            isCallActive = true;
            micBtn.classList.add('active');
            micContainer.classList.add('is-speaking');
            callStateText.textContent = "Listening / Speaking...";
            setStatus('active', 'Call Active');
        });

        vapi.on('call-end', () => {
            isCallActive = false;
            micBtn.classList.remove('active');
            micContainer.classList.remove('is-speaking');
            callStateText.textContent = "Call Ended";
            setStatus('connected', 'Ready to Call');
            setTimeout(() => {
                if(!isCallActive) callStateText.textContent = "Ready to connect";
            }, 2000);
        });

        vapi.on('message', (message) => {
            if (message.type === 'transcript' && message.transcriptType === 'final') {
                appendMessage(message.role, message.transcript);
            }
        });

        vapi.on('error', (e) => {
            console.error("Vapi Error:", e);
            setStatus('error', 'Error Occurred');
            callStateText.textContent = "An error occurred.";
            isCallActive = false;
            micBtn.classList.remove('active');
            micContainer.classList.remove('is-speaking');
        });

    } catch (err) {
        console.error("Failed to initialize Vapi", err);
        setStatus('error', 'Init Failed');
    }
}

function toggleCall() {
    if (!vapi) return;
    
    const asstId = assistantIdInput.value.trim();
    if (!asstId) {
        alert("Assistant ID is missing.");
        return;
    }

    if (isCallActive) {
        callStateText.textContent = "Disconnecting...";
        vapi.stop();
    } else {
        callStateText.textContent = "Connecting...";
        vapi.start(asstId);
    }
}

function appendMessage(role, text) {
    if (transcriptPlaceholder) {
        transcriptPlaceholder.style.display = 'none';
    }

    const msgDiv = document.createElement('div');
    msgDiv.classList.add('message');
    msgDiv.classList.add(role === 'user' ? 'user' : 'agent');
    
    // Capitalize first letter of role
    const sender = role === 'user' ? 'You' : 'Agent';
    msgDiv.innerHTML = `<strong>${sender}:</strong> ${text}`;
    
    transcriptBox.appendChild(msgDiv);
    transcriptBox.scrollTop = transcriptBox.scrollHeight;
}

function setStatus(state, text) {
    statusIndicator.className = 'status-indicator ' + state;
    statusText.textContent = text;
}
