import sqlite3
import os
from flask import Flask, render_template, request, jsonify, send_file
import re
import unicodedata
from copy import deepcopy
import threading
import uuid
import queue
import time
import random
import string
from openpyxl import Workbook
from io import BytesIO
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')

# --------- Database setup (SQLite for local development) ----------
def get_db_connection():
    # Render provides DATABASE_URL environment variable
    database_url = os.environ.get('DATABASE_URL')
    
    if database_url:
        # Fix for Render's PostgreSQL URL
        if database_url.startswith("postgres://"):
            database_url = database_url.replace("postgres://", "postgresql://", 1)
        
        # Production - use PostgreSQL
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(database_url)
        return conn
    else:
        # Local development - use SQLite
        conn = sqlite3.connect('local_orders.db')
        conn.row_factory = sqlite3.Row
        return conn

def update_db_schema():
    """Update existing database schema to add missing columns"""
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if we're using PostgreSQL or SQLite
    is_postgres = os.environ.get('DATABASE_URL') is not None
    
    try:
        # Check if status column exists
        if is_postgres:
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name='confirmed_orders' and column_name='status'
            """)
        else:
            cur.execute("PRAGMA table_info(confirmed_orders)")
            columns = [row[1] for row in cur.fetchall()]
        
        if is_postgres:
            column_exists = cur.fetchone() is not None
        else:
            column_exists = 'status' in columns
            
        if not column_exists:
            print("Adding status column to confirmed_orders table...")
            if is_postgres:
                cur.execute("ALTER TABLE confirmed_orders ADD COLUMN status VARCHAR(20) DEFAULT 'confirmed'")
            else:
                cur.execute("ALTER TABLE confirmed_orders ADD COLUMN status TEXT DEFAULT 'confirmed'")
            conn.commit()
            print("Status column added successfully")
        else:
            print("Status column already exists")
            
        # Check if order_group column exists and its type
        if is_postgres:
            cur.execute("""
                SELECT column_name, data_type, character_maximum_length
                FROM information_schema.columns 
                WHERE table_name='confirmed_orders' and column_name='order_group'
            """)
        else:
            cur.execute("PRAGMA table_info(confirmed_orders)")
            columns = {row[1]: row for row in cur.fetchall()}
        
        if is_postgres:
            order_group_info = cur.fetchone()
            order_group_exists = order_group_info is not None
            if order_group_exists:
                current_type = order_group_info[1]
                current_length = order_group_info[2]
                print(f"order_group column exists: {current_type}({current_length})")
                
                # If it's varchar(50), let's alter it to varchar(255)
                if current_type == 'character varying' and current_length == 50:
                    print("Altering order_group column from VARCHAR(50) to VARCHAR(255)...")
                    cur.execute("ALTER TABLE confirmed_orders ALTER COLUMN order_group TYPE VARCHAR(255)")
                    conn.commit()
                    print("order_group column altered to VARCHAR(255) successfully")
        else:
            order_group_exists = 'order_group' in columns
            
        if not order_group_exists:
            print("Adding order_group column to confirmed_orders table...")
            if is_postgres:
                cur.execute("ALTER TABLE confirmed_orders ADD COLUMN order_group VARCHAR(255) DEFAULT 'main'")
            else:
                cur.execute("ALTER TABLE confirmed_orders ADD COLUMN order_group TEXT DEFAULT 'main'")
            conn.commit()
            print("order_group column added successfully")
        else:
            print("order_group column already exists")
            
    except Exception as e:
        print(f"Schema update error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

def init_db():
    """Initialize database tables"""
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check if we're using PostgreSQL or SQLite
        is_postgres = os.environ.get('DATABASE_URL') is not None
        
        if is_postgres:
            # PostgreSQL tables
            cur.execute('''
                CREATE TABLE IF NOT EXISTS confirmed_orders (
                    id SERIAL PRIMARY KEY,
                    session_id VARCHAR(255) NOT NULL,
                    product VARCHAR(255) NOT NULL,
                    quantity INTEGER NOT NULL,
                    status VARCHAR(20) DEFAULT 'pending',
                    order_group VARCHAR(255) DEFAULT 'main',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            # SQLite tables
            cur.execute('''
                CREATE TABLE IF NOT EXISTS confirmed_orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    product TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    order_group TEXT DEFAULT 'main',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        conn.commit()
    except Exception as e:
        print(f"Database initialization error: {e}")
    finally:
        cur.close()
        conn.close()
        
# Initialize database on startup
init_db()
update_db_schema()







# ---------- Core Order Processing Functions (UNCHANGED) ----------
# [Keep all your existing functions: normalize, levenshtein_distance, similarity_percentage, 
#  parse_number_words, separate_numbers_and_words, extract_numbers_and_positions, 
#  find_associated_number, parse_order_interactive]

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

def find_associated_number(product_position, all_tokens, numbers_with_positions, used_number_positions):
    """Find the number associated with a product based on word order patterns"""
    if not numbers_with_positions:
        return 1, None
    
    # Filter out used numbers
    available_numbers = [(pos, val) for pos, val in numbers_with_positions if pos not in used_number_positions]
    
    if not available_numbers:
        return 1, None
    
    # **PRIORITY 1: Number immediately before the product (most common pattern in Portuguese)**
    if product_position > 0:
        prev_token = all_tokens[product_position - 1]
        if prev_token.isdigit() or prev_token in word2num_all:
            for pos, val in available_numbers:
                if pos == product_position - 1:
                    return val, pos
    
    # **PRIORITY 2: Look for the closest number that comes BEFORE the product**
    numbers_before = [(pos, val) for pos, val in available_numbers if pos < product_position]
    if numbers_before:
        # Return the closest number before the product (highest position number before product)
        closest_before = max(numbers_before, key=lambda x: x[0])
        return closest_before[1], closest_before[0]
    
    # **PRIORITY 3: Number immediately after the product (less common)**
    if product_position + 1 < len(all_tokens):
        next_token = all_tokens[product_position + 1]
        if next_token.isdigit() or next_token in word2num_all:
            for pos, val in available_numbers:
                if pos == product_position + 1:
                    return val, pos
    
    # **PRIORITY 4: Look for numbers after the product (least preferred)**
    numbers_after = [(pos, val) for pos, val in available_numbers if pos > product_position]
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
    used_number_positions = set()    # Track used number positions

    # Create a list to store all potential product matches with their positions
    potential_matches = []
    
    # First pass: find all potential product matches and their positions
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
                potential_matches.append({
                    'start_pos': i,
                    'end_pos': i + size - 1,
                    'product': best_product,
                    'original_idx': best_original_idx,
                    'score': best_score
                })
                
                # Mark positions as used for this iteration
                for j in range(size):
                    used_positions.add(i + j)
                
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
                potential_matches.append({
                    'start_pos': i,
                    'end_pos': i,
                    'product': best_match,
                    'original_idx': best_original_idx,
                    'score': best_score
                })
                        
                used_positions.add(i)
                matched = True
            
            i += 1

    # Reset used_positions for the second pass
    used_positions.clear()
    
    # **SECOND PASS: Process matches in order of their relationship to numbers**
    # Sort potential matches by their proximity to available numbers
    def get_match_priority(match):
        start_pos = match['start_pos']
        
        # Check for number immediately before (highest priority)
        if start_pos > 0:
            prev_token = tokens[start_pos - 1]
            if (prev_token.isdigit() or prev_token in word2num_all) and (start_pos - 1) not in used_number_positions:
                return 0  # Highest priority
        
        # Check for any number before
        numbers_before = [pos for pos, _ in numbers_with_positions if pos < start_pos and pos not in used_number_positions]
        if numbers_before:
            return 1
        
        # Check for number immediately after
        if start_pos + 1 < len(tokens):
            next_token = tokens[start_pos + 1]
            if (next_token.isdigit() or next_token in word2num_all) and (start_pos + 1) not in used_number_positions:
                return 2
        
        # Check for any number after
        numbers_after = [pos for pos, _ in numbers_with_positions if pos > start_pos and pos not in used_number_positions]
        if numbers_after:
            return 3
        
        return 4  # No number association
    
    # Sort matches by priority
    potential_matches.sort(key=get_match_priority)
    
    # Process matches in priority order
    for match in potential_matches:
        quantity, number_position = find_associated_number(
            match['start_pos'], tokens, numbers_with_positions, used_number_positions
        )
        
        # Update the working database (add to existing quantity)
        working_db[match['original_idx']][1] += quantity
        parsed_orders.append({
            "product": match['product'], 
            "qty": quantity, 
            "score": round(match['score'], 2)
        })
        
        # Mark product positions as used
        for pos in range(match['start_pos'], match['end_pos'] + 1):
            used_positions.add(pos)
        
        # Mark number as used if one was found
        if number_position is not None:
            used_number_positions.add(number_position)

    return parsed_orders, working_db


# ---------- Initialize products_db ----------
products_db = [
    ["limão", 0],
    ["abacaxi", 0], ["abacaxi com hortelã", 0], ["açaí", 0], ["acerola", 0],
    ["ameixa", 0], ["cajá", 0], ["cajú", 0], ["goiaba", 0], ["graviola", 0],
    ["manga", 0], ["maracujá", 0], ["morango", 0], ["seriguela", 0], ["tamarindo", 0],
    ["caixa de ovos", 0], ["ovo", 0], ["queijo", 0]
]






# ---------- Enhanced OrderBot with Database Persistence ----------
user_sessions = {}
session_lock = threading.Lock()

class OrderSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.products_db = deepcopy(products_db)
        self.current_db = deepcopy(products_db)
        self.confirmed_orders = []
        self.pending_orders = []
        
        self.state = "waiting_for_next"
        self.reminder_count = 0
        self.message_queue = queue.Queue()
        self.active_timer = None
        self.last_activity = time.time()
        self.waiting_for_option = False

    def _save_final_orders(self, orders_list, status="confirmed", order_group="main"):
        """Save orders with order_group support"""
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check if we're using PostgreSQL or SQLite
        is_postgres = os.environ.get('DATABASE_URL') is not None
        
        for order in orders_list:
            for product, qty in order.items():
                if qty > 0:
                    if is_postgres:
                        cur.execute(
                            'INSERT INTO confirmed_orders (session_id, product, quantity, status, order_group) VALUES (%s, %s, %s, %s, %s)',
                            (self.session_id, product, qty, status, order_group)
                        )
                    else:
                        cur.execute(
                            'INSERT INTO confirmed_orders (session_id, product, quantity, status, order_group) VALUES (?, ?, ?, ?, ?)',
                            (self.session_id, product, qty, status, order_group)
                        )
        
        conn.commit()
        cur.close()
        conn.close()

    def get_global_orders(self):
        """Get all confirmed orders from database with separate auto-confirmed groups"""
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check if we're using PostgreSQL or SQLite
        is_postgres = os.environ.get('DATABASE_URL') is not None
        
        # Get main confirmed orders (blue boxes)
        if is_postgres:
            cur.execute('''
                SELECT product, SUM(quantity) as total_quantity 
                FROM confirmed_orders 
                WHERE status = %s AND order_group = %s 
                GROUP BY product 
                ORDER BY total_quantity DESC
            ''', ('confirmed', 'main'))
        else:
            cur.execute('''
                SELECT product, SUM(quantity) as total_quantity 
                FROM confirmed_orders 
                WHERE status = ? AND order_group = ? 
                GROUP BY product 
                ORDER BY total_quantity DESC
            ''', ('confirmed', 'main'))
        
        main_orders_data = cur.fetchall()
        
        # Get auto-confirmed order groups (yellow boxes)
        if is_postgres:
            cur.execute('''
                SELECT order_group, product, quantity 
                FROM confirmed_orders 
                WHERE status = %s AND order_group != %s 
                ORDER BY order_group, product
            ''', ('auto_confirmed', 'main'))
        else:
            cur.execute('''
                SELECT order_group, product, quantity 
                FROM confirmed_orders 
                WHERE status = ? AND order_group != ? 
                ORDER BY order_group, product
            ''', ('auto_confirmed', 'main'))
        
        auto_orders_data = cur.fetchall()
        
        # Process main orders (blue)
        main_orders = {}
        for row in main_orders_data:
            product = row[0]
            quantity = row[1]
            if product and quantity:
                main_orders[product] = quantity
        
        # Process auto orders (yellow boxes grouped by order_group)
        auto_orders = {}
        for row in auto_orders_data:
            order_group = row[0]
            product = row[1]
            quantity = row[2]
            
            if order_group not in auto_orders:
                auto_orders[order_group] = {}
            
            auto_orders[order_group][product] = quantity
        
        cur.close()
        conn.close()
        
        return {
            'main_orders': main_orders,
            'auto_orders': auto_orders
        }

    def get_all_orders_summary(self):
        """Get summary of all orders from database (for Excel download)"""
        return self.get_global_orders()

    # ... (rest of your OrderSession methods remain exactly the same)
    # [Keep all your existing start_new_conversation, add_item, process_message, etc. methods]

    def start_new_conversation(self):
        """Reset for a new conversation and wait for next message"""
        self.current_db = deepcopy(self.products_db)
        self.state = "waiting_for_next"
        self.reminder_count = 0
        self.waiting_for_option = False
        self._cancel_timer()
        self.message_queue.put("🔄 **Conversa reiniciada!**")
        
    def add_item(self, parsed_orders):
        """Add parsed items to current database - simplified"""
        for order in parsed_orders:
            for idx, (product, _) in enumerate(self.current_db):
                if product == order["product"]:
                    self.current_db[idx][1] += order["qty"]
                    break
        
        self.state = "collecting"
        self._start_inactivity_timer()

    def reset_cycle(self, parsed_orders):
        """Reset cycle and add items during confirmation phase - simplified"""
        self._cancel_timer()
        
        for order in parsed_orders:
            for idx, (product, _) in enumerate(self.current_db):
                if product == order["product"]:
                    self.current_db[idx][1] += order["qty"]
                    break
        
        self.state = "collecting"
        self.reminder_count = 0
        self._start_inactivity_timer()
            
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
        """Start reminder cycle - first reminder after 30 seconds"""
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
                self._mark_as_pending()
            else:
                self.reminder_count += 1
                self._cancel_timer()
                self.active_timer = threading.Timer(30.0, self._send_reminder)
                self.active_timer.daemon = True
                self.active_timer.start()
                
    
    def _mark_as_pending(self):
        """Mark current order as auto-confirmed with unique order group"""
        if self.has_items():
            auto_order = self.get_current_orders()
            # Generate shorter unique order group ID
            import random
            import string
            timestamp = str(int(time.time()))[-6:]  # Last 6 digits of timestamp
            random_part = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
            order_group_id = f"auto_{timestamp}_{random_part}"
            
            # Save as auto-confirmed with unique group
            self._save_final_orders([auto_order], status="auto_confirmed", order_group=order_group_id)
            self.message_queue.put("🟡 **PEDIDO CONFIRMADO AUTOMATICAMENTE** - O pedido foi salvo e aguarda sua confirmação final na barra lateral.")
            self._reset_current()
            self.state = "waiting_for_next"  # Go back to waiting_for_next state


    
    def _build_summary(self):
        """Build summary message"""
        summary = "📋 **RESUMO DO SEU PEDIDO:**\n"
        for product, qty in self.get_current_orders().items():
            if qty > 0:
                summary += f"• {product}: {qty}\n"
        summary += "\n⚠️ **Confirma o pedido?** (responda com 'confirmar' ou 'nao')"
        return summary
    
    def _check_cancel_command(self, message_lower):
        """Check if message contains cancel commands"""
        cancel_commands = ['cancelar', 'hoje não', 'hoje nao']
        return any(command in message_lower for command in cancel_commands)
    
    def process_message(self, message):
        """Process incoming message"""
        message_lower = message.lower().strip()
        self.last_activity = time.time()
        
        # Check for cancel commands in ANY state
        if self._check_cancel_command(message_lower):
            self.start_new_conversation()
            return {
                'success': True,
                'message': None
            }
        
        # Handle waiting_for_next state
        if self.state == "waiting_for_next":
            self.state = "option"
            self.waiting_for_option = True
            return {
                'success': True,
                'message': "🔄 **Conversa reiniciada!**\n\nVocê quer pedir(1) ou falar com o gerente(2)?"
            }
        
        # Handle option state
        if self.state == "option" and self.waiting_for_option:
            if message_lower == "1":
                self.waiting_for_option = False
                self.state = "collecting"
                self._start_inactivity_timer()
                return {
                    'success': True,
                    'message': "Ótimo! Digite seus pedidos. Ex: '2 mangas e 3 queijos'"
                }
            elif message_lower == "2":
                self.waiting_for_option = False
                self.state = "waiting_for_next"
                return {
                    'success': True,
                    'message': "Ok então."
                }
            else:
                return {
                    'success': False,
                    'message': "Por favor, escolha uma opção: 1 para pedir ou 2 para falar com o gerente."
                }
        
        # Handle pending confirmation state
        if self.state == "pending_confirmation":
            if any(word in message_lower.split() for word in ['confirmar', 'sim', 's']):
                if self.pending_orders:
                    self.confirmed_orders.extend(self.pending_orders)
                    self._save_final_orders(self.pending_orders)
                    pending_count = len(self.pending_orders)
                    self.pending_orders = []
                    self.state = "collecting"
                    self._start_inactivity_timer()
                    return {
                        'success': True,
                        'message': f"✅ **PEDIDO PENDENTE CONFIRMADO!** {pending_count} pedido(s) adicionado(s) à lista."
                    }
                elif any(word in message_lower.split() for word in ['cancelar', 'nao', 'não', 'n']):
                    # Add cancellation logic here - clear pending orders and reset state
                    self.pending_orders = []
                    self.state = "collecting"
                    self._start_inactivity_timer()
                    return {
                        'success': True,
                        'message': "🔄 Pedidos pendentes cancelados. Continue adicionando itens."
                    }
                else:
                    return {
                        'success': False,
                        'message': "❌ Por favor, confirme ou cancele o pedido pendente. Digite 'confirmar' para confirmar ou 'cancelar' para cancelar."
                    }
            else:
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
                self.confirmed_orders.append(confirmed_order)
                self._save_final_orders([confirmed_order])
                self._reset_current()
                
                response = "✅ **PEDIDO CONFIRMADO COM SUCESSO!**\n\n**Itens confirmados:**\n"
                for product, qty in confirmed_order.items():
                    if qty > 0:
                        response += f"• {qty}x {product}\n"
                response += "\nObrigado pelo pedido! 🎉"
                
                return {
                    'success': True,
                    'message': response
                }
            elif any(word in message_lower.split() for word in ['nao', 'não', 'n']):
                self._cancel_timer()
                self._reset_current()
                self._start_inactivity_timer()
                return {
                    'success': True, 
                    'message': "🔄 **Lista limpa!** Digite novos itens."
                }
            else:
                parsed_orders, updated_db = parse_order_interactive(message, self.current_db)
                if parsed_orders:
                    self.current_db = updated_db
                    self._cancel_timer()
                    self.state = "collecting"
                    self.reminder_count = 0
                    self._start_inactivity_timer()
                    return {'success': True}
                else:
                    return {
                        'success': False,
                        'message': "❌ Item não reconhecido. Digite 'confirmar' para confirmar ou 'nao' para cancelar."
                    }
        
        # Handle collection state
        elif self.state in ["collecting"]:
            if message_lower in ['pronto', 'confirmar']:
                if self.has_items():
                    self._send_summary()
                    return {'success': True, 'message': "📋 Preparando seu resumo..."}
                else:
                    return {'success': False, 'message': "❌ Lista vazia. Adicione itens primeiro."}
            else:
                parsed_orders, updated_db = parse_order_interactive(message, self.current_db)
                self.current_db = updated_db
                if parsed_orders:
                    self._start_inactivity_timer()
                    return {'success': True}
                else:
                    self._start_inactivity_timer()
                    return {'success': False, 'message': "❌ Nenhum item reconhecido. Tente usar termos como '2 mangas', 'cinco queijos', etc."}
        
        return {'success': False, 'message': "Estado não reconhecido. Digite 'cancelar' para reiniciar."}
        
    def has_items(self):
        """Check if there are any items in the order"""
        return any(qty > 0 for _, qty in self.current_db)
    
    def get_current_orders(self):
        """Get current orders as dict"""
        return {product: qty for product, qty in self.current_db if qty > 0}
    
    def _reset_current(self):
        """Reset current session (temp items) completely"""
        self.current_db = deepcopy(self.products_db)
        self.state = "collecting"
        self.reminder_count = 0
        self._cancel_timer()
    
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

# ---------- Flask routes (unchanged) ----------
@app.route("/")
def index():
    session_id = request.args.get('session_id', str(uuid.uuid4()))
    # Get global orders to display in sidebar
    session = OrderSession("global")
    global_orders = session.get_global_orders()
    return render_template("index.html", session_id=session_id, global_orders=global_orders)

@app.route("/download_excel", methods=["GET"])
def download_excel():
    """Generate Excel file from database"""
    session = OrderSession("global")
    orders = session.get_all_orders_summary()
    
    # Create Excel file in memory
    wb = Workbook()
    ws = wb.active
    ws.append(["Produto", "Quantidade"])
    
    for product, quantity in orders.items():
        ws.append([product, quantity])
    
    # Save to BytesIO object
    excel_file = BytesIO()
    wb.save(excel_file)
    excel_file.seek(0)
    
    return send_file(
        excel_file,
        as_attachment=True,
        download_name='pedidos.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

@app.route("/global_orders", methods=["GET"])
def get_global_orders():
    """API endpoint to get global orders for AJAX updates"""
    session = OrderSession("global")
    global_orders = session.get_global_orders()
    return jsonify(global_orders)

@app.route("/send_message", methods=["POST"])
def send_message():
    data = request.json
    message = data.get("message", "").strip()
    session_id = data.get("session_id", "default")
    
    if not message:
        return jsonify({'error': 'Mensagem vazia'})
    
    session = get_user_session(session_id)
    result = session.process_message(message)
    
    response = {
        'status': session.state,
        'current_orders': session.get_current_orders(),
        'confirmed_orders': session.confirmed_orders,
        'pending_orders': session.pending_orders
    }
    

    # estude melhor isso aqui:
    if result.get('message'):
        response['bot_message'] = result['message']
    
    return jsonify(response)

@app.route("/get_updates", methods=["POST"])
def get_updates():
    """Get updates including pending messages and session state"""
    data = request.json
    print(data)
    session_id = data.get("session_id", "default")
    
    session = get_user_session(session_id)
    pending_message = session.get_pending_message()
    
    response = {
        'state': session.state,
        'current_orders': session.get_current_orders(),
        'confirmed_orders': session.confirmed_orders,
        'pending_orders': session.pending_orders,
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
    return jsonify({
        'current_orders': session.get_current_orders(),
        'confirmed_orders': session.confirmed_orders,
        'pending_orders': session.pending_orders
    })

@app.route("/confirm_auto_order", methods=["POST"])
def confirm_auto_order():
    """Move auto-confirmed order to main confirmed orders"""
    data = request.json
    order_group = data.get("order_group")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if we're using PostgreSQL or SQLite
    is_postgres = os.environ.get('DATABASE_URL') is not None
    
    if is_postgres:
        # Update status and order_group to move to main orders
        cur.execute(
            'UPDATE confirmed_orders SET status = %s, order_group = %s WHERE order_group = %s AND status = %s',
            ('confirmed', 'main', order_group, 'auto_confirmed')
        )
    else:
        cur.execute(
            'UPDATE confirmed_orders SET status = ?, order_group = ? WHERE order_group = ? AND status = ?',
            ('confirmed', 'main', order_group, 'auto_confirmed')
        )
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route("/delete_auto_order", methods=["POST"])
def delete_auto_order():
    """Delete an auto-confirmed order group"""
    data = request.json
    order_group = data.get("order_group")
    
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Check if we're using PostgreSQL or SQLite
    is_postgres = os.environ.get('DATABASE_URL') is not None
    
    if is_postgres:
        cur.execute('DELETE FROM confirmed_orders WHERE order_group = %s AND status = %s', (order_group, 'auto_confirmed'))
    else:
        cur.execute('DELETE FROM confirmed_orders WHERE order_group = ? AND status = ?', (order_group, 'auto_confirmed'))
    
    conn.commit()
    cur.close()
    conn.close()
    
    return jsonify({'success': True})

@app.route("/confirm_order", methods=["POST"])
def confirm_order():
    data = request.json
    order_id = data.get("order_id")
    # Update database: set status='confirmed' where id=order_id

@app.route("/reset_session", methods=["POST"])
def reset_session():
    """Reset session manually"""
    data = request.json
    session_id = data.get("session_id", "default")
    
    session = get_user_session(session_id)
    session.start_new_conversation()
    
    return jsonify({'success': True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
