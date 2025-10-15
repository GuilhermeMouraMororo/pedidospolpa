let sessionId = 'default';

function updateOrdersDisplay(data) {
    const confirmedOrders = document.getElementById('confirmedOrders');
    const pendingOrders = document.getElementById('pendingOrders');
    const confirmedCount = document.getElementById('confirmedCount');
    const pendingCount = document.getElementById('pendingCount');
    
    // Update confirmed orders - handle object format
    if (data.confirmed_orders && Object.keys(data.confirmed_orders).length > 0) {
        confirmedCount.textContent = Object.keys(data.confirmed_orders).length;
        confirmedOrders.innerHTML = Object.entries(data.confirmed_orders).map(([product, quantity]) => 
            `<div class="order-item confirmed-item">
                <span class="product-name">${product}</span>
                <span class="product-quantity confirmed-quantity">${quantity}</span>
            </div>`
        ).join('');
    } else {
        confirmedCount.textContent = '0';
        confirmedOrders.innerHTML = '<div class="empty-orders">Nenhum pedido confirmado</div>';
    }
    
    // Update pending orders - handle object format
    if (data.pending_orders && Object.keys(data.pending_orders).length > 0) {
        pendingCount.textContent = Object.keys(data.pending_orders).length;
        pendingOrders.innerHTML = Object.entries(data.pending_orders).map(([product, quantity]) => 
            `<div class="order-item pending-item">
                <span class="product-name">${product}</span>
                <span class="product-quantity pending-quantity">${quantity}</span>
            </div>`
        ).join('');
    } else {
        pendingCount.textContent = '0';
        pendingOrders.innerHTML = '<div class="empty-orders">Nenhum pedido pendente</div>';
    }
}

function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    
    if (!message) return;
    
    // Add user message to chat
    addMessage(message, 'user');
    input.value = '';
    
    // Show typing indicator
    showTypingIndicator();
    
    // Send to server
    fetch('/send_message', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            message: message,
            session_id: sessionId
        })
    })
    .then(response => response.json())
    .then(data => {
        hideTypingIndicator();
        addMessage(data.response, 'bot');
        updateOrdersDisplay(data.orders);
    })
    .catch(error => {
        hideTypingIndicator();
        addMessage('Erro de conexão. Tente novamente.', 'bot');
        console.error('Error:', error);
    });
}

function addMessage(text, sender) {
    const chatMessages = document.getElementById('chatMessages');
    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${sender}-message`;
    messageDiv.textContent = text;
    chatMessages.appendChild(messageDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

function showTypingIndicator() {
    document.getElementById('typingIndicator').style.display = 'block';
    document.getElementById('chatMessages').scrollTop = document.getElementById('chatMessages').scrollHeight;
}

function hideTypingIndicator() {
    document.getElementById('typingIndicator').style.display = 'none';
}

function downloadExcel() {
    window.open('/download_excel', '_blank');
}

// Enter key to send message
document.getElementById('messageInput').addEventListener('keypress', function(e) {
    if (e.key === 'Enter') {
        sendMessage();
    }
});

// Load initial orders when page loads
document.addEventListener('DOMContentLoaded', function() {
    fetch(`/get_updates?session_id=${sessionId}`)
        .then(response => response.json())
        .then(orders => updateOrdersDisplay(orders));
});
