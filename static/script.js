let sessionId = 'default';

function updateOrdersDisplay(data) {
    // ... existing confirmed orders code ...
    
    // Update pending orders with confirm buttons
    if (data.pending_orders && data.pending_orders.length > 0) {
        pendingCount.textContent = data.pending_orders.length;
        pendingOrders.innerHTML = data.pending_orders.map((order, index) => 
            `<div style="margin-bottom: 15px;">
                <div style="font-weight: bold; color: #92400e; margin-bottom: 5px; font-size: 14px;">
                    Pedido Pendente ${index + 1}:
                    <button class="confirm-btn" onclick="confirmPendingOrder(${index})" 
                            style="background: #10b981; color: white; border: none; padding: 4px 8px; border-radius: 4px; font-size: 12px; margin-left: 10px; cursor: pointer;">
                        ✅ Confirmar
                    </button>
                </div>
                ${Object.entries(order).map(([product, quantity]) => 
                    `<div class="order-item pending-item">
                        <span class="product-name">${product}</span>
                        <span class="product-quantity pending-quantity">${quantity}</span>
                    </div>`
                ).join('')}
            </div>`
        ).join('');
    } else {
        pendingCount.textContent = '0';
        pendingOrders.innerHTML = '<div class="empty-orders">Nenhum pedido pendente</div>';
    }
}

async function confirmPendingOrder(orderIndex) {
    try {
        const response = await fetch('/confirm_pending_order', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                session_id: sessionId,
                order_index: orderIndex
            })
        });
        
        const data = await response.json();
        
        if (data.success) {
            addMessage("✅ **PEDIDO PENDENTE CONFIRMADO!** O pedido foi movido para confirmados.", 'bot', 'success');
            // Refresh the display
            checkUpdates();
            loadGlobalOrders();
        } else {
            addMessage("❌ Erro ao confirmar pedido: " + data.message, 'bot', 'alert');
        }
    } catch (error) {
        console.error('Error confirming order:', error);
        addMessage('❌ Erro de conexão ao confirmar pedido.', 'bot', 'alert');
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
