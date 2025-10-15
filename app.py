import os
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, jsonify, send_file
import re
import unicodedata
from copy import deepcopy
import threading
import uuid
import queue
import time

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'

# --------- Database setup ----------
def get_db_connection():
    """Get database connection with better error handling"""
    try:
        database_url = os.environ.get('DATABASE_URL')
        if database_url:
            conn = psycopg2.connect(database_url)
            return conn
        else:
            # Fall back to Excel if no database is available
            print("No DATABASE_URL environment variable found")
            return None
    except Exception as e:
        print(f"Database connection failed: {e}")
        return None

def init_db():
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_name TEXT NOT NULL,
            order_details TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

def update_db_schema():
    """Update existing database to add order_type column"""
    conn = get_db_connection()
    if conn is None:
        print("No database connection available for schema update")
        return
        
    try:
        cur = conn.cursor()
        
        # Check if order_type column exists
        cur.execute('''
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name='orders' and column_name='order_type'
        ''')
        
        if not cur.fetchone():
            print("Adding order_type column to orders table...")
            cur.execute('ALTER TABLE orders ADD COLUMN order_type VARCHAR(20) DEFAULT \'confirmed\'')
            conn.commit()
            print("Database schema updated successfully")
        else:
            print("order_type column already exists")
            
        cur.close()
        conn.close()
    except Exception as e:
        print(f"Error updating database schema: {e}")

# Initialize database on startup
init_db()
update_db_schema()

# ---------- Core Order Processing Functions ----------

def normalize(text):
    text = text.lower()
    text = ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn')
    return text.strip() 

def levenshtein_distance(a, b):
    m, n = len(a), len(b)
    if m == 0: return n
    if n == 0: return m
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1):
        dp[i][0] = i
    for j in range(n + 1):
        dp[0][j] = j    
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,
                dp[i][j - 1] + 1,
                dp[i - 1][j - 1] + cost
            )
    return dp[m][n]

def similarity_percentage(a, b):
    a, b = normalize(a), normalize(b)
    distance = levenshtein_distance(a, b)
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 100.0
    return (1 - distance / max_len) * 100

units = {
    "0":0, "1":1, "2":2, "3":3, "4":4, "5":5, "6":6, "7":7, "8":8, "9":9,
    "zero":0, "um":1, "uma":1, "dois":2, "duas":2, "dos":2, "tres":3, "tres":3, "treis": 3,
    "quatro":4, "quarto":4, "cinco":5, "cnico": 5, "seis":6, "ses":6, "sete":7, "oito":8, "nove":9, "nov": 9
}
teens = {
    "dez":10, "onze":11, "doze":12, "treze":13, "quatorze":14, "catorze":14,
    "quinze":15, "dezesseis":16, "dezessete":17, "dezoito":18, "dezenove":19
}
tens = {
    "vinte":20, "trinta":30, "quarenta":40, "cinquenta":50, "sessenta":60,
    "setenta":70, "oitenta":80, "noventa":90
}
hundreds = {
    "cem":100, "cento":100, "duzentos":200, "trezentos":300, "quatrocentos":400,
    "quinhentos":500, "seiscentos":600, "setecentos":700, "oitocentos":800,
    "novecentos":900
}
word2num_all = {**units, **teens, **tens, **hundreds}

def parse_number_words(tokens):
    """Parse list of number-word tokens (no 'e' tokens) into integer (supports up to 999)."""
    total = 0
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in hundreds:
            total += hundreds[t]
            i += 1
        elif t in tens:
            val = tens[t]
            if i + 1 < len(tokens) and tokens[i+1] in units:
                val += units[tokens[i+1]]
                i += 2
            else:
                i += 1
            total += val
        elif t in teens:
            total += teens[t]; i += 1
        elif t in units:
            total += units[t]; i += 1
        else:
            i += 1
    return total if total > 0 else None

def separate_numbers_and_words(text):
    """Insert spaces between digit-word and between number-words glued to words."""
    text = text.lower()
    text = re.sub(r"(\d+)([a-zA-Z])", r"\1 \2", text)
    text = re.sub(r"([a-zA-Z])(\d+)", r"\1 \2", text)

    # Protect compound teen numbers so they don't get split
    protected_teens = ["dezesseis", "dezessete", "dezoito", "dezenove"]
    for teen in protected_teens:
        text = text.replace(teen, f" {teen} ")

    # Now process other number words normally
    keys = sorted(word2num_all.keys(), key=len, reverse=True)
    for w in keys:
        if w not in protected_teens:
            text = re.sub(rf"\b{re.escape(w)}\b", f" {w} ", text)

    text = re.sub(r"\s+", " ", text).strip()
    return text

def extract_numbers_and_positions(tokens):
    """Extract all numbers and their positions from tokens"""
    numbers = []
    
    i = 0
    while i < len(tokens):
        if tokens[i].isdigit():
            numbers.append((i, int(tokens[i])))
            i += 1
        elif tokens[i] in word2num_all:
            # Only combine number words if they're connected by "e"
            num_tokens = [tokens[i]]
            j = i + 1
            
            # Look for "e" followed by a number word
            while j < len(tokens) - 1:
                if tokens[j] == "e" and tokens[j+1] in word2num_all:
                    num_tokens.extend([tokens[j], tokens[j+1]])
                    j += 2
                else:
                    break
            
            # Parse the number tokens
            number = parse_number_words([t for t in num_tokens if t != "e"])
            if number:
                numbers.append((i, number))
                i = j
            else:
                i += 1
        else:
            i += 1
            
    return numbers

def find_associated_number(product_position, all_tokens, numbers_with_positions):
    """Find the number associated with a product based on word order patterns"""
    if not numbers_with_positions:
        return 1, None
    
    # Pattern 1: Number immediately before the product (most common)
    if product_position > 0:
        prev_token = all_tokens[product_position - 1]
        if prev_token.isdigit() or prev_token in word2num_all:
            for pos, val in numbers_with_positions:
                if pos == product_position - 1:
                    return val, pos
    
    # Pattern 2: Look for numbers before the product (anywhere before)
    numbers_before = [(pos, val) for pos, val in numbers_with_positions if pos < product_position]
    if numbers_before:
        # Return the closest number before the product (highest position number before product)
        closest_before = max(numbers_before, key=lambda x: x[0])
        return closest_before[1], closest_before[0]
    
    # Pattern 3: Number immediately after the product
    if product_position + 1 < len(all_tokens):
        next_token = all_tokens[product_position + 1]
        if next_token.isdigit() or next_token in word2num_all:
            for pos, val in numbers_with_positions:
                if pos == product_position + 1:
                    return val, pos
    
    # Pattern 4: Look for numbers after the product (anywhere after)
    numbers_after = [(pos, val) for pos, val in numbers_with_positions if pos > product_position]
    if numbers_after:
        # Return the closest number after the product (lowest position number after product)
        closest_after = min(numbers_after, key=lambda x: x[0])
        return closest_after[1], closest_after[0]
    
    return 1, None

def parse_order_interactive(message, products_db, similarity_threshold=80, uncertain_range=(60, 80)):
    """
    Interactive version that uses pattern-based quantity association with multi-word product support.
    Fixed to handle multiple products with quantities in the same message.
    """
    message = normalize(message)
    message = separate_numbers_and_words(message)
    message = re.sub(r"[,\.;\+\-\/\(\)\[\]\:]", " ", message)
    message = re.sub(r"\s+", " ", message).strip()

    tokens = message.split()
    
    # Start with the current database state (accumulate items)
    working_db = deepcopy(products_db)
    parsed_orders = []

    # Extract all numbers and their positions
    numbers_with_positions = extract_numbers_and_positions(tokens)
    
    # Sort products by word count (longest first) to prioritize multi-word matches
    product_names = [p for p, _ in products_db]
    sorted_products = sorted([(p, i) for i, p in enumerate(product_names)], 
                           key=lambda x: len(x[0].split()), reverse=True)
    max_prod_words = max(len(p.split()) for p in product_names)

    # Precompute the set of words that appear in any product name
    product_words = set()
    for product in product_names:
        for word in product.split():
            product_words.add(normalize(word))

    used_positions = set()  # Track used token positions
    used_numbers = set()    # Track used number positions

    i = 0
    while i < len(tokens):
        if i in used_positions:
            i += 1
            continue

        token = tokens[i]

        # Skip filler words and numbers only if they are not part of a product name
        filler_words = {"quero", "e"}
        if (token in filler_words and token not in product_words) or (token.isdigit() and i not in [pos for pos, _ in numbers_with_positions]) or token in word2num_all:
            i += 1
            continue

        matched = False
        
        # Try different phrase lengths (longest first) - prioritize multi-word products
        for size in range(min(max_prod_words, 4), 0, -1):
            if i + size > len(tokens):
                continue
                
            # Skip if any token in the phrase is already used or is a number/filler (unless part of product)
            phrase_tokens = tokens[i:i+size]
            skip_phrase = False
            for j in range(size):
                if i+j in used_positions:
                    skip_phrase = True
                    break
                t = tokens[i+j]
                if (t.isdigit() or t in word2num_all or (t in filler_words and t not in product_words)):
                    skip_phrase = True
                    break
                    
            if skip_phrase:
                continue
                
            phrase = " ".join(phrase_tokens)
            phrase_norm = normalize(phrase)

            best_score = 0
            best_product = None
            best_original_idx = None
            
            # Find best match for this phrase length (check against sorted products)
            for idx, (prod_name, orig_idx) in enumerate(sorted_products):
                prod_norm = normalize(prod_name)
                score = similarity_percentage(phrase_norm, prod_norm)
                if score > best_score:
                    best_score = score
                    best_product = prod_name
                    best_original_idx = orig_idx

            # Handle the match
            if best_score >= similarity_threshold:
                # Find associated number for this product
                quantity, number_position = find_associated_number(i, tokens, numbers_with_positions)
                
                # If number position is already used, try to find another number
                if number_position is not None and number_position in used_numbers:
                    # Look for any unused number
                    for pos, val in numbers_with_positions:
                        if pos not in used_numbers:
                            quantity = val
                            number_position = pos
                            break
                
                # Update the working database (add to existing quantity)
                working_db[best_original_idx][1] += quantity
                parsed_orders.append({"product": best_product, "qty": quantity, "score": round(best_score,2)})
                
                # Mark positions as used
                for j in range(size):
                    used_positions.add(i + j)
                if number_position is not None:
                    used_numbers.add(number_position)
                
                i += size
                matched = True
                break

        if not matched:
            # If no match found, find the best match to suggest
            phrase = tokens[i]
            best_match = None
            best_score = 0
            best_original_idx = None
            phrase_norm = normalize(phrase)
            
            for idx, product in enumerate(product_names):
                score = similarity_percentage(phrase_norm, normalize(product))
                if score > best_score:
                    best_score = score
                    best_match = product
                    best_original_idx = idx
            
            if best_match and best_score > 50:
                # Auto-confirm reasonable matches for web version
                quantity, number_position = find_associated_number(i, tokens, numbers_with_positions)
                
                # If number position is already used, try to find another number
                if number_position is not None and number_position in used_numbers:
                    for pos, val in numbers_with_positions:
                        if pos not in used_numbers:
                            quantity = val
                            number_position = pos
                            break
                
                # Update the working database (add to existing quantity)
                working_db[best_original_idx][1] += quantity
                parsed_orders.append({
                    "product": best_match,
                    "qty": quantity,
                    "score": round(best_score, 2)
                })
                        
                used_positions.add(i)
                if number_position is not None:
                    used_numbers.add(number_position)
                
                matched = True
            
            i += 1

    return parsed_orders, working_db



# ---------- Initialize products_db ----------
products_db = [
    ["limão"],
    ["abacaxi"], ["abacaxi com hortelã"], ["açaí"], ["acerola"],
    ["ameixa"], ["cajá"], ["cajú"], ["goiaba"], ["graviola"],
    ["manga"], ["maracujá"], ["morango"], ["seriguela"], ["tamarindo"],
    ["caixa de ovos"], ["ovo"], ["queijo"]
]

for product in products_db:
    product.append(0)

# ---------- Enhanced OrderBot with New Requirements ----------
user_sessions = {}
session_lock = threading.Lock()

class OrderSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.products_db = deepcopy(products_db)
        self.current_db = deepcopy(products_db)
        
        # Remove the old confirmed_orders and pending_orders attributes
        # We'll use the database for all public orders
        
        self.state = "collecting"
        self.reminder_count = 0
        self.message_queue = queue.Queue()
        self.active_timer = None
        self.last_activity = time.time()
        self.waiting_for_option = False

    def start_new_conversation(self):
        """Reset for a new conversation and wait for next message"""
        self.current_db = deepcopy(self.products_db)
        self.state = "waiting_for_next"
        self.reminder_count = 0
        self.waiting_for_option = False
        self._cancel_timer()
        # Put the restart message in queue
        self.message_queue.put("🔄 **Conversa reiniciada!**")

    # === SIMPLE ORDER MANAGEMENT ===
    def add_item(self, parsed_orders):
        """Add parsed items to current session"""
        for order in parsed_orders:
            for idx, (product, _) in enumerate(self.current_db):
                if product == order["product"]:
                    self.current_db[idx][1] += order["qty"]
                    break
        self.state = "collecting"
        self._start_inactivity_timer()

    def has_items(self):
        """Check if there are any items in the current order"""
        return any(qty > 0 for _, qty in self.current_db)
    
    def get_current_orders(self):
        """Get current session orders as dict"""
        return {product: qty for product, qty in self.current_db if qty > 0}

    def _reset_current(self):
        """Reset current session completely"""
        self.current_db = deepcopy(self.products_db)
        self.state = "collecting"
        self.reminder_count = 0
        self._cancel_timer()

    # === PUBLIC ORDER STORAGE (Database) ===
    def save_public_orders(self, orders_list, order_type="confirmed"):
        """Save orders to public database"""
        conn = get_db_connection()
        if conn is None:
            print("No database connection available")
            return False
            
        try:
            cur = conn.cursor()
            for order in orders_list:
                for product, qty in order.items():
                    if qty > 0:
                        cur.execute(
                            'INSERT INTO orders (session_id, product, quantity, order_type) VALUES (%s, %s, %s, %s)',
                            (self.session_id, product, qty, order_type)
                        )
            conn.commit()
            cur.close()
            conn.close()
            print(f"Saved {len(orders_list)} orders as {order_type}")
            return True
        except Exception as e:
            print(f"Error saving to database: {e}")
            return False

    def get_all_public_orders(self):
        """Get ALL public orders from database (everyone sees these)"""
        try:
            conn = get_db_connection()
            if conn is None:
                return {"confirmed": {}, "pending": {}}
                
            cur = conn.cursor()
            
            # Get confirmed orders
            cur.execute('''
                SELECT product, SUM(quantity) as total 
                FROM orders 
                WHERE order_type = 'confirmed'
                GROUP BY product 
                ORDER BY product
            ''')
            confirmed = {product: total for product, total in cur.fetchall() if total > 0}
            
            # Get pending orders  
            cur.execute('''
                SELECT product, SUM(quantity) as total 
                FROM orders 
                WHERE order_type = 'pending'
                GROUP BY product 
                ORDER BY product
            ''')
            pending = {product: total for product, total in cur.fetchall() if total > 0}
            
            cur.close()
            conn.close()
            
            return {"confirmed": confirmed, "pending": pending}
            
        except Exception as e:
            print(f"Error loading public orders: {e}")
            return {"confirmed": {}, "pending": {}}

    # === TIMER AND REMINDER SYSTEM ===
    def _start_inactivity_timer(self):
        """Start 30-second inactivity timer"""
        self._cancel_timer()
        self.active_timer = threading.Timer(30.0, self._send_summary)
        self.active_timer.daemon = True
        self.active_timer.start()
    
    def _cancel_timer(self):
        """Cancel active timer"""
        if self.active_timer:
            self.active_timer.cancel()
            self.active_timer = None
    
    def _send_summary(self):
        """Send summary and start confirmation cycle"""
        if self.state == "collecting" and self.has_items():
            self.state = "confirming"
            self.reminder_count = 0
            summary = self._build_summary()
            self.message_queue.put(summary)
            self._start_reminder_cycle()
        elif self.state == "collecting":
            self._start_inactivity_timer()
    
    def _start_reminder_cycle(self):
        """Start reminder cycle"""
        self.reminder_count = 1
        self._cancel_timer()
        self.active_timer = threading.Timer(30.0, self._send_reminder)
        self.active_timer.daemon = True
        self.active_timer.start()
    
    def _send_reminder(self):
        """Send a reminder"""
        if self.state == "confirming" and self.reminder_count <= 5:
            summary = self._build_summary()
            self.message_queue.put(f"🔔 **LEMBRETE ({self.reminder_count}/5):**\n{summary}")
            
            if self.reminder_count == 5:
                # After 5th reminder, mark as pending in PUBLIC database
                self._mark_as_pending()
            else:
                self.reminder_count += 1
                self._cancel_timer()
                self.active_timer = threading.Timer(30.0, self._send_reminder)
                self.active_timer.daemon = True
                self.active_timer.start()

    def _mark_as_pending(self):
        """Mark current order as pending in PUBLIC database"""
        if self.has_items():
            pending_order = self.get_current_orders()
            # Save to PUBLIC database
            self.save_public_orders([pending_order], "pending")
            self.message_queue.put("🟡 **PEDIDO MARCADO COMO PENDENTE** - Aguardando confirmação.\n\nDigite 'confirmar' para confirmar este pedido.")
            self._reset_current()
            self.state = "pending_confirmation"

    def _build_summary(self):
        """Build summary message"""
        summary = "📋 **RESUMO DO SEU PEDIDO:**\n"
        for product, qty in self.get_current_orders().items():
            if qty > 0:
                summary += f"• {product}: {qty}\n"
        summary += "\n⚠️ **Confirma o pedido?** (responda com 'confirmar' ou 'nao')"
        return summary

    # === MESSAGE PROCESSING ===
    def process_message(self, message):
        """Process incoming message"""
        message_lower = message.lower().strip()
        self.last_activity = time.time()
        
        # Check for cancel commands
        cancel_commands = ['cancelar', 'hoje não', 'hoje nao']
        if any(command in message_lower for command in cancel_commands):
            self._reset_current()
            self.message_queue.put("🔄 **Conversa reiniciada!**")
            return {'success': True, 'message': None}
        
        # Handle waiting_for_next state
        if self.state == "waiting_for_next":
            self.state = "option"
            self.waiting_for_option = True
            return {'success': True, 'message': "🔄 **Conversa reiniciada!**\n\nVocê quer pedir(1) ou falar com o gerente(2)?"}
        
        # Handle option state
        if self.state == "option" and self.waiting_for_option:
            if message_lower == "1":
                self.waiting_for_option = False
                self.state = "collecting"
                self._start_inactivity_timer()
                return {'success': True, 'message': "Ótimo! Digite seus pedidos. Ex: '2 mangas e 3 queijos'"}
            elif message_lower == "2":
                self.waiting_for_option = False
                self.state = "waiting_for_next"
                return {'success': True, 'message': "Ok então."}
            else:
                return {'success': False, 'message': "Por favor, escolha uma opção: 1 para pedir ou 2 para falar com o gerente."}
        
        # Handle pending confirmation state
        if self.state == "pending_confirmation":
            if any(word in message_lower.split() for word in ['confirmar', 'sim', 's']):
                # Move pending orders to confirmed in PUBLIC database
                public_orders = self.get_all_public_orders()
                if public_orders["pending"]:
                    # Convert pending orders to confirmed
                    pending_list = [{product: qty} for product, qty in public_orders["pending"].items()]
                    self.save_public_orders(pending_list, "confirmed")
                    # Clear pending orders (they're now confirmed)
                    # Note: In a real app, you might want to mark them as confirmed instead of deleting
                    self.state = "collecting"
                    self._start_inactivity_timer()
                    return {'success': True, 'message': "✅ **PEDIDOS PENDENTES CONFIRMADOS!** Adicionados à lista pública."}
                else:
                    self.state = "collecting"
                    self._start_inactivity_timer()
                    return {'success': True, 'message': "🔄 Nenhum pedido pendente. Continue adicionando itens."}
            else:
                # Continue with normal order processing
                self.state = "collecting"
                self._start_inactivity_timer()
                parsed_orders, updated_db = parse_order_interactive(message, self.current_db)
                self.current_db = updated_db
                if parsed_orders:
                    return {'success': True}
                else:
                    return {'success': True, 'message': "❌ Nenhum item reconhecido. Tente usar termos como '2 mangas', 'cinco queijos', etc."}
        
        # Handle confirmation state
        if self.state == "confirming":
            if any(word in message_lower.split() for word in ['confirmar', 'sim', 's']):
                self._cancel_timer()
                confirmed_order = self.get_current_orders()
                # Save to PUBLIC database as confirmed
                self.save_public_orders([confirmed_order], "confirmed")
                self._reset_current()
                
                response = "✅ **PEDIDO CONFIRMADO COM SUCESSO!**\n\n**Itens confirmados:**\n"
                for product, qty in confirmed_order.items():
                    if qty > 0:
                        response += f"• {product}: {qty}\n"
                response += "\nObrigado pelo pedido! 🎉"
                return {'success': True, 'message': response}
                
            elif any(word in message_lower.split() for word in ['nao', 'não', 'n']):
                self._cancel_timer()
                self._reset_current()
                self._start_inactivity_timer()
                return {'success': True, 'message': "🔄 **Lista limpa!** Digite novos itens."}
            else:
                # Try to parse as product order
                parsed_orders, updated_db = parse_order_interactive(message, self.current_db)
                if parsed_orders:
                    self.current_db = updated_db
                    self._cancel_timer()
                    self.state = "collecting"
                    self.reminder_count = 0
                    self._start_inactivity_timer()
                    return {'success': True}
                else:
                    return {'success': False, 'message': "❌ Item não reconhecido. Digite 'confirmar' para confirmar ou 'nao' para cancelar."}
        
        # Handle collection state (normal ordering)
        elif self.state in ["collecting"]:
            if message_lower in ['pronto', 'confirmar']:
                if self.has_items():
                    self._send_summary()
                    return {'success': True, 'message': "📋 Preparando seu resumo..."}
                else:
                    return {'success': False, 'message': "❌ Lista vazia. Adicione itens primeiro."}
            else:
                # Parse product order
                parsed_orders, updated_db = parse_order_interactive(message, self.current_db)
                self.current_db = updated_db
                if parsed_orders:
                    self._start_inactivity_timer()
                    return {'success': True}
                else:
                    self._start_inactivity_timer()
                    return {'success': False, 'message': "❌ Nenhum item reconhecido. Tente usar termos como '2 mangas', 'cinco queijos', etc."}
        
        # Default fallback
        return {'success': False, 'message': "Estado não reconhecido. Digite 'cancelar' para reiniciar."}

    def get_pending_message(self):
        """Get pending message if any"""
        try:
            return self.message_queue.get_nowait()
        except queue.Empty:
            return None


def get_user_session(session_id):
    """Get or create user session"""
    with session_lock:
        if session_id not in user_sessions:
            user_sessions[session_id] = OrderSession(session_id)
        return user_sessions[session_id]



# ---------- Flask routes ----------
@app.route('/')
def index():
    return redirect(url_for('orders'))

@app.route('/orders')
def orders():
    # Get all orders
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    
    # Get pending orders
    c.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC")
    pending_orders = c.fetchall()
    
    # Get completed orders
    c.execute("SELECT * FROM orders WHERE status = 'completed' ORDER BY updated_at DESC")
    completed_orders = c.fetchall()
    
    conn.close()
    
    return render_template('orders.html', 
                         pending_orders=pending_orders,
                         completed_orders=completed_orders)
    
@app.route('/')
def index():
    return redirect(url_for('orders'))

@app.route('/orders')
def orders():
    # Get all orders
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    
    # Get pending orders
    c.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC")
    pending_orders = c.fetchall()
    
    # Get completed orders
    c.execute("SELECT * FROM orders WHERE status = 'completed' ORDER BY updated_at DESC")
    completed_orders = c.fetchall()
    
    conn.close()
    
    return render_template('orders.html', 
                         pending_orders=pending_orders,
                         completed_orders=completed_orders)

@app.route('/add_order', methods=['POST'])
def add_order():
    if request.method == 'POST':
        customer_name = request.form.get('customer_name', '').strip()
        order_details = request.form.get('order_details', '').strip()
        
        if customer_name and order_details:
            conn = sqlite3.connect('orders.db')
            c = conn.cursor()
            c.execute(
                "INSERT INTO orders (customer_name, order_details) VALUES (?, ?)",
                (customer_name, order_details)
            )
            conn.commit()
            conn.close()
        
        return redirect(url_for('orders'))

@app.route('/update_order_status/<int:order_id>', methods=['POST'])
def update_order_status(order_id):
    new_status = request.form.get('status')
    
    if new_status in ['pending', 'completed']:
        conn = sqlite3.connect('orders.db')
        c = conn.cursor()
        c.execute(
            "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (new_status, order_id)
        )
        conn.commit()
        conn.close()
    
    return redirect(url_for('orders'))

@app.route('/delete_order/<int:order_id>', methods=['POST'])
def delete_order(order_id):
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    c.execute("DELETE FROM orders WHERE id = ?", (order_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('orders'))

@app.route('/api/orders')
def api_orders():
    status = request.args.get('status', 'all')
    
    conn = sqlite3.connect('orders.db')
    c = conn.cursor()
    
    if status == 'pending':
        c.execute("SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at DESC")
    elif status == 'completed':
        c.execute("SELECT * FROM orders WHERE status = 'completed' ORDER BY updated_at DESC")
    else:
        c.execute("SELECT * FROM orders ORDER BY created_at DESC")
    
    orders = c.fetchall()
    conn.close()
    
    # Convert to list of dictionaries for JSON
    orders_list = []
    for order in orders:
        orders_list.append({
            'id': order[0],
            'customer_name': order[1],
            'order_details': order[2],
            'status': order[3],
            'created_at': order[4],
            'updated_at': order[5]
        })
    
    return jsonify(orders_list)

@app.route("/download_excel", methods=["GET"])
def download_excel():
    """Generate Excel file from ALL database orders"""
    from openpyxl import Workbook
    from io import BytesIO
    
    try:
        conn = get_db_connection()
        if conn is None:
            return "Database not available", 500
            
        cur = conn.cursor()
        
        # Get all confirmed orders
        cur.execute('''
            SELECT product, SUM(quantity) as total_quantity 
            FROM orders 
            WHERE order_type = 'confirmed'
            GROUP BY product 
            ORDER BY product
        ''')
        confirmed_orders = cur.fetchall()
        
        # Get all pending orders
        cur.execute('''
            SELECT product, SUM(quantity) as total_quantity 
            FROM orders 
            WHERE order_type = 'pending'
            GROUP BY product 
            ORDER BY product
        ''')
        pending_orders = cur.fetchall()
        
        cur.close()
        conn.close()
        
        # Create Excel file
        wb = Workbook()
        
        # Confirmed orders sheet
        ws_confirmed = wb.active
        ws_confirmed.title = "Pedidos Confirmados"
        ws_confirmed.append(["Produto", "Quantidade"])
        for product, quantity in confirmed_orders:
            if quantity > 0:
                ws_confirmed.append([product, quantity])
        
        # Pending orders sheet
        ws_pending = wb.create_sheet("Pedidos Pendentes")
        ws_pending.append(["Produto", "Quantidade"])
        for product, quantity in pending_orders:
            if quantity > 0:
                ws_pending.append([product, quantity])
        
        # Save to BytesIO
        excel_file = BytesIO()
        wb.save(excel_file)
        excel_file.seek(0)
        
        return send_file(
            excel_file,
            as_attachment=True,
            download_name='pedidos_completos.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        print(f"Error generating Excel: {e}")
        return "Error generating Excel file", 500

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    
    if not message:
        return jsonify({'error': 'Mensagem vazia'})
    
    session = get_user_session(session_id)
    result = session.process_message(message)
    
    # Get ALL public orders from database
    public_orders = session.get_all_public_orders()
    
    response = {
        'status': session.state,
        'current_orders': session.get_current_orders(),
        'confirmed_orders': public_orders["confirmed"],
        'pending_orders': public_orders["pending"]
    }
    
    if result.get('message'):
        response['bot_message'] = result['message']
    
    return jsonify(response)

@app.route("/get_updates", methods=["POST"])
def get_updates():
    """Get updates including pending messages and session state"""
    data = request.json
    session_id = data.get("session_id", "default")
    
    session = get_user_session(session_id)
    pending_message = session.get_pending_message()
    
    # Get ALL public orders from database
    public_orders = session.get_all_public_orders()
    
    response = {
        'state': session.state,
        'current_orders': session.get_current_orders(),
        'confirmed_orders': public_orders["confirmed"],
        'pending_orders': public_orders["pending"],
        'reminders_sent': session.reminder_count,
        'has_message': pending_message is not None
    }
    
    if pending_message:
        response['bot_message'] = pending_message
    
    return jsonify(response)
    
@app.route("/get_orders", methods=["GET"])
def get_orders():
    session_id = request.args.get("session_id", "default")
    session = get_user_session(session_id)
    
    # Get ALL public orders from database
    public_orders = session.get_all_public_orders()
    
    return jsonify({
        'current_orders': session.get_current_orders(),
        'confirmed_orders': public_orders["confirmed"],
        'pending_orders': public_orders["pending"]
    })
    
@app.route("/reset_session", methods=["POST"])
def reset_session():
    """Reset session manually"""
    data = request.json
    session_id = data.get("session_id", "default")
    
    session = get_user_session(session_id)
    session.start_new_conversation()
    
    return jsonify({'success': True})

if __name__ == "__main__":
    app.run(debug=True, host='0.0.0.0', port=5000, use_reloader=False)
