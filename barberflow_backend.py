# -*- coding: utf-8 -*-
import os
import psycopg2
import psycopg2.extras 
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from datetime import datetime, date, timedelta
from babel.dates import format_date, format_datetime

# --- 1. CONFIGURAÇÃO E CONEXÃO COM POSTGRESQL ---
# ATENÇÃO: Substitua estas variáveis pelas suas credenciais reais do PostgreSQL.
DB_CONFIG = {
    'database': os.environ.get('PG_DB', 'barberflow_db'),
    'user': os.environ.get('PG_USER', 'postgres'),
    'password': os.environ.get('PG_PASSWORD', 'wordKey##'),
    'host': os.environ.get('PG_HOST', 'localhost'),
    'port': os.environ.get('PG_PORT', '5433')
}

# Chave secreta para sessões do Flask. MUDE ESTA CHAVE em produção!
FLASK_SECRET_KEY = 'super_secret_key_para_barberflow_session'
ADMIN_KEY = 'barberflowadmin'
FIXED_EXPENSES = 1500.00 # Exemplo de despesa fixa mensal

# --- Configurações de Horário para Agendamento ---
SHOP_HOURS = [
    ("09:00", "12:00"), # Manhã
    ("14:00", "18:00")  # Tarde
]
# Intervalo base para checagem de slots (todos os slots de agendamento devem ser múltiplos deste)
SLOT_INTERVAL_MINUTES = 15 

def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Erro ao conectar ao banco de dados: {e}")
        return None

# --- 2. INICIALIZAÇÃO DO FLASK ---
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

# --- 3. FUNÇÕES DE BANCO DE DADOS ---

def setup_database():
    """Cria as tabelas se não existirem."""
    conn = get_db_connection()
    if conn is None:
        return
    try:
        cur = conn.cursor()
        
        # Tabela de Serviços
        cur.execute("""
            CREATE TABLE IF NOT EXISTS services (
                id SERIAL PRIMARY KEY,
                name VARCHAR(100) NOT NULL UNIQUE,
                duration_minutes INTEGER NOT NULL,
                price NUMERIC(10, 2) NOT NULL
            );
        """)
        
        # Tabela de Agendamentos (reservas)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS appointments (
                id SERIAL PRIMARY KEY,
                client_name VARCHAR(100) NOT NULL,
                client_phone VARCHAR(20),
                service_id INTEGER REFERENCES services(id) ON DELETE SET NULL,
                appointment_date DATE NOT NULL,
                appointment_time TIME NOT NULL,
                status VARCHAR(20) DEFAULT 'Pendente' CHECK (status IN ('Pendente', 'Confirmado', 'Concluído', 'Cancelado')),
                archived BOOLEAN DEFAULT FALSE, -- Novo campo para arquivamento
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Tabela de Despesas
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id SERIAL PRIMARY KEY,
                description VARCHAR(255) NOT NULL,
                amount NUMERIC(10, 2) NOT NULL,
                expense_date DATE DEFAULT CURRENT_DATE,
                is_fixed BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        
        conn.commit()
    except Exception as e:
        print(f"Erro ao configurar o banco de dados: {e}")
    finally:
        if conn:
            conn.close()

# Garante que as tabelas existam ao iniciar
setup_database()

# --- 4. FUNÇÕES DE CÁLCULO DE SLOTS ---

def generate_time_slots(service_duration):
    """Gera todos os slots de horário disponíveis para o dia, baseando-se no intervalo e duração do serviço."""
    slots = []
    
    # Gerar todos os slots possíveis baseados no intervalo mínimo (ex: 15 minutos)
    all_possible_slots = []
    for start_hour, end_hour in SHOP_HOURS:
        start = datetime.strptime(start_hour, "%H:%M")
        end = datetime.strptime(end_hour, "%H:%M")
        
        current = start
        while current < end:
            all_possible_slots.append(current.strftime("%H:%M"))
            current += timedelta(minutes=SLOT_INTERVAL_MINUTES)
    
    # Filtrar slots que se encaixam na duração do serviço
    for slot_time_str in all_possible_slots:
        slot_dt = datetime.strptime(slot_time_str, "%H:%M")
        end_time = (slot_dt + timedelta(minutes=service_duration)).strftime("%H:%M")
        
        # Verificar se o slot final está dentro de qualquer um dos blocos de horário da barbearia
        is_valid = False
        for start_hour, end_hour in SHOP_HOURS:
            shop_start = datetime.strptime(start_hour, "%H:%M").time()
            shop_end = datetime.strptime(end_hour, "%H:%M").time()
            
            # O slot precisa começar DEPOIS ou IGUAL ao início do bloco
            # E terminar ANTES ou IGUAL ao fim do bloco
            if slot_dt.time() >= shop_start and datetime.strptime(end_time, "%H:%M").time() <= shop_end:
                is_valid = True
                break
                
        if is_valid:
            slots.append(slot_time_str)
            
    return slots

def get_booked_slots(date_str, service_duration):
    """Busca slots já ocupados para uma data e calcula o bloqueio de slots subsequentes."""
    conn = get_db_connection()
    if conn is None:
        return set()

    booked_slots = set()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Busca agendamentos Confirmados ou Pendentes que NÃO estão arquivados
        cur.execute("""
            SELECT appointment_time, s.duration_minutes 
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE appointment_date = %s 
              AND status IN ('Pendente', 'Confirmado')
              AND archived = FALSE
        """, (date_str,))
        
        appointments = cur.fetchall()

        for app in appointments:
            start_time_str = str(app['appointment_time'])[:5]
            booked_slots.add(start_time_str)
            
            # Bloqueia slots subsequentes que se sobreporiam ao agendamento
            app_duration = app['duration_minutes']
            start_dt = datetime.strptime(start_time_str, "%H:%M")
            
            # Calcula quantos slots (de 15 min) o serviço ocupa
            num_slots = int(app_duration / SLOT_INTERVAL_MINUTES)
            
            # Bloqueia os slots de intervalo subsequentes (excluindo o slot de início, já adicionado)
            for i in range(1, num_slots):
                blocked_time = start_dt + timedelta(minutes=i * SLOT_INTERVAL_MINUTES)
                booked_slots.add(blocked_time.strftime("%H:%M"))
                
    except Exception as e:
        print(f"Erro ao buscar slots ocupados: {e}")
    finally:
        if conn:
            conn.close()
            
    return booked_slots

# --- 5. ROTAS DA API (FLASK) ---

@app.route('/api/services', methods=['GET'])
def get_services():
    """Retorna a lista de todos os serviços cadastrados."""
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        cur.execute("SELECT id, name, duration_minutes, price FROM services ORDER BY name;")
        services = cur.fetchall()
        
        # Converte para lista de dicionários serializáveis
        services_list = [dict(service) for service in services]
        
        return jsonify(services_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/available-slots', methods=['GET'])
def get_available_slots():
    """Retorna os slots disponíveis para uma data e duração de serviço específica."""
    date_str = request.args.get('date')
    service_duration = request.args.get('duration', type=int)

    if not date_str or not service_duration:
        return jsonify({"error": "Data e duração do serviço são obrigatórias"}), 400

    # 1. Checar se a data é hoje ou futura
    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify({"error": "Formato de data inválido"}), 400
        
    today = date.today()
    
    if target_date < today:
        return jsonify({"slots": []}) # Não permite agendar no passado
        
    # 2. Gera todos os slots possíveis para a duração do serviço
    all_slots = generate_time_slots(service_duration)
    
    # 3. Busca slots já ocupados (e bloqueados por sobreposição)
    booked_slots = get_booked_slots(date_str, service_duration)
    
    # 4. Filtra slots
    available_slots = []
    
    for slot in all_slots:
        is_available = True
        
        # Verifica se o slot atual está na lista de bloqueados
        if slot in booked_slots:
            is_available = False

        # Se for hoje, checa se o horário já passou
        if target_date == today:
            now = datetime.now()
            slot_dt = datetime.strptime(f"{date_str} {slot}", "%Y-%m-%d %H:%M")
            if slot_dt < now:
                is_available = False
                
        if is_available:
            available_slots.append(slot)
            
    return jsonify({"slots": available_slots})

@app.route('/api/appointments', methods=['POST'])
def create_appointment():
    """Cria um novo agendamento."""
    data = request.json
    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    service_id = data.get('service_id', type=int)
    appointment_date = data.get('appointment_date')
    appointment_time = data.get('appointment_time')

    if not all([client_name, service_id, appointment_date, appointment_time]):
        return jsonify({"error": "Dados incompletos"}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor()
        
        # 1. Verificar se o slot ainda está disponível
        # Primeiro, obter a duração do serviço
        cur.execute("SELECT duration_minutes FROM services WHERE id = %s", (service_id,))
        service_duration_row = cur.fetchone()
        if not service_duration_row:
            return jsonify({"error": "Serviço inválido"}), 400
        service_duration = service_duration_row[0]
        
        # Gerar slots bloqueados
        booked_slots = get_booked_slots(appointment_date, service_duration)

        if appointment_time in booked_slots:
            return jsonify({"error": "O horário selecionado não está mais disponível."}), 409 # Conflict

        # 2. Inserir o agendamento
        cur.execute("""
            INSERT INTO appointments (client_name, client_phone, service_id, appointment_date, appointment_time, status)
            VALUES (%s, %s, %s, %s, %s, 'Confirmado') RETURNING id;
        """, (client_name, client_phone, service_id, appointment_date, appointment_time))
        
        new_id = cur.fetchone()[0]
        conn.commit()
        
        return jsonify({"message": "Agendamento confirmado com sucesso!", "id": new_id, "status": "Confirmado"}), 201
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

# --- 6. ROTAS DE ADMINISTRAÇÃO ---

@app.route('/api/admin/appointments', methods=['GET'])
def get_admin_appointments():
    """Retorna todos os agendamentos (não arquivados) para o admin."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401
        
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Buscar agendamentos que NÃO estão arquivados
        cur.execute("""
            SELECT 
                a.id, a.client_name, a.client_phone, s.name as service_name, 
                a.appointment_date, a.appointment_time, a.status, s.price 
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE a.archived = FALSE
            ORDER BY a.appointment_date, a.appointment_time;
        """)
        
        appointments = cur.fetchall()
        
        # Formatar a data para exibição (necessário para a visualização HTML/JS)
        appointments_list = []
        for app in appointments:
            app_dict = dict(app)
            # Formata a data para o padrão DD/MM/AAAA
            app_dict['appointment_date_formatted'] = format_date(app_dict['appointment_date'], 'dd/MM/yyyy', locale='pt_BR')
            # Formata o preço para R$ X.XX
            app_dict['price_formatted'] = f"R$ {app_dict['price']:.2f}".replace('.', ',')
            
            appointments_list.append(app_dict)
        
        return jsonify(appointments_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/appointments/<int:app_id>', methods=['PUT'])
def update_appointment(app_id):
    """Atualiza o status de um agendamento."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401
        
    data = request.json
    status = data.get('status')
    
    if status not in ['Pendente', 'Confirmado', 'Concluído', 'Cancelado']:
        return jsonify({"error": "Status inválido"}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE appointments SET status = %s WHERE id = %s;
        """, (status, app_id))
        conn.commit()
        
        return jsonify({"message": f"Status do agendamento {app_id} atualizado para {status}"}), 200
        
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/services', methods=['POST'])
def add_service():
    """Adiciona um novo serviço."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401
        
    data = request.json
    name = data.get('name')
    duration = data.get('duration_minutes', type=int)
    price = data.get('price', type=float)

    if not all([name, duration, price]):
        return jsonify({"error": "Dados de serviço incompletos"}), 400

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO services (name, duration_minutes, price)
            VALUES (%s, %s, %s);
        """, (name, duration, price))
        conn.commit()
        
        return jsonify({"message": "Serviço adicionado com sucesso!"}), 201
        
    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Serviço com este nome já existe."}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/services/<int:service_id>', methods=['PUT', 'DELETE'])
def manage_service(service_id):
    """Edita ou deleta um serviço."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401
        
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor()
        
        if request.method == 'PUT':
            data = request.json
            name = data.get('name')
            duration = data.get('duration_minutes', type=int)
            price = data.get('price', type=float)

            if not all([name, duration, price]):
                return jsonify({"error": "Dados de serviço incompletos"}), 400
                
            cur.execute("""
                UPDATE services SET name = %s, duration_minutes = %s, price = %s
                WHERE id = %s;
            """, (name, duration, price, service_id))
            
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({"error": "Serviço não encontrado"}), 404
            
            conn.commit()
            return jsonify({"message": "Serviço atualizado com sucesso!"}), 200
            
        elif request.method == 'DELETE':
            # Antes de deletar, atualiza agendamentos que usavam este serviço
            # para garantir integridade referencial (service_id = NULL)
            cur.execute("UPDATE appointments SET service_id = NULL WHERE service_id = %s", (service_id,))
            
            cur.execute("DELETE FROM services WHERE id = %s;", (service_id,))
            
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({"error": "Serviço não encontrado"}), 404

            conn.commit()
            return jsonify({"message": "Serviço excluído com sucesso!"}), 200

    except psycopg2.errors.UniqueViolation:
        conn.rollback()
        return jsonify({"error": "Serviço com este nome já existe."}), 409
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/expenses', methods=['GET', 'POST', 'DELETE'])
def manage_expenses():
    """Gerencia despesas."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401
        
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        
        if request.method == 'GET':
            # Retorna todas as despesas
            cur.execute("""
                SELECT id, description, amount, expense_date, is_fixed 
                FROM expenses 
                ORDER BY expense_date DESC, created_at DESC;
            """)
            expenses = cur.fetchall()
            expenses_list = []
            for exp in expenses:
                exp_dict = dict(exp)
                exp_dict['expense_date_formatted'] = format_date(exp_dict['expense_date'], 'dd/MM/yyyy', locale='pt_BR')
                exp_dict['amount_formatted'] = f"R$ {exp_dict['amount']:.2f}".replace('.', ',')
                expenses_list.append(exp_dict)
            
            return jsonify(expenses_list)
            
        elif request.method == 'POST':
            data = request.json
            description = data.get('description')
            amount = data.get('amount', type=float)
            is_fixed = data.get('is_fixed', type=bool)
            expense_date_str = data.get('expense_date')

            if not all([description, amount]):
                return jsonify({"error": "Dados de despesa incompletos"}), 400

            expense_date = datetime.strptime(expense_date_str, '%Y-%m-%d').date() if expense_date_str else date.today()

            cur.execute("""
                INSERT INTO expenses (description, amount, expense_date, is_fixed)
                VALUES (%s, %s, %s, %s) RETURNING id;
            """, (description, amount, expense_date, is_fixed))
            new_id = cur.fetchone()[0]
            conn.commit()
            
            return jsonify({"message": "Despesa registrada com sucesso!", "id": new_id}), 201
            
        elif request.method == 'DELETE':
            exp_id = request.args.get('id', type=int)
            if not exp_id:
                return jsonify({"error": "ID da despesa é obrigatório"}), 400
                
            cur.execute("DELETE FROM expenses WHERE id = %s;", (exp_id,))
            
            if cur.rowcount == 0:
                conn.rollback()
                return jsonify({"error": "Despesa não encontrada"}), 404

            conn.commit()
            return jsonify({"message": "Despesa excluída com sucesso!"}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/finance', methods=['GET'])
def get_finance_report():
    """Retorna o relatório financeiro consolidado."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401

    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not start_date_str or not end_date_str:
        return jsonify({"error": "Datas de início e fim são obrigatórias"}), 400
        
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor()
        
        # 1. Receita (apenas agendamentos 'Concluído' dentro do período)
        cur.execute("""
            SELECT COALESCE(SUM(s.price), 0.00) 
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE a.status = 'Concluído' 
              AND a.appointment_date BETWEEN %s AND %s;
        """, (start_date_str, end_date_str))
        revenue = cur.fetchone()[0]
        
        # 2. Despesas (apenas despesas registradas dentro do período)
        cur.execute("""
            SELECT COALESCE(SUM(amount), 0.00) 
            FROM expenses
            WHERE expense_date BETWEEN %s AND %s;
        """, (start_date_str, end_date_str))
        expenses = cur.fetchone()[0]
        
        # 3. Lucro
        profit = revenue - expenses
        
        # Formata para Real Brasileiro
        def format_currency(value):
            return f"R$ {value:.2f}".replace('.', ',')
        
        report = {
            "revenue": format_currency(revenue),
            "expenses": format_currency(expenses),
            "profit": format_currency(profit)
        }
        
        return jsonify(report)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/archive/<int:app_id>', methods=['POST'])
def archive_appointment(app_id):
    """Arquiva um agendamento (move para histórico/relatório)."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401

    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500
        
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE appointments SET archived = TRUE 
            WHERE id = %s;
        """, (app_id,))
        conn.commit()
        return jsonify({"message": f"Agendamento {app_id} arquivado com sucesso."}), 200
    except Exception as e:
        conn.rollback()
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()

@app.route('/api/admin/archived-appointments', methods=['GET'])
def get_archived_appointments():
    """Retorna a lista de agendamentos arquivados (histórico)."""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({"error": "Não autorizado"}), 401
        
    conn = get_db_connection()
    if conn is None:
        return jsonify({"error": "Falha na conexão com o banco de dados"}), 500

    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        # Buscar agendamentos que ESTÃO arquivados
        cur.execute("""
            SELECT 
                a.id, a.client_name, a.client_phone, s.name as service_name, 
                a.appointment_date, a.appointment_time, a.status, s.price 
            FROM appointments a
            LEFT JOIN services s ON a.service_id = s.id -- LEFT JOIN para casos sem service_id (serviço deletado)
            WHERE a.archived = TRUE
            ORDER BY a.appointment_date DESC, a.appointment_time DESC;
        """)
        
        appointments = cur.fetchall()
        
        appointments_list = []
        for app in appointments:
            app_dict = dict(app)
            app_dict['appointment_date_formatted'] = format_date(app_dict['appointment_date'], 'dd/MM/yyyy', locale='pt_BR')
            app_dict['price_formatted'] = f"R$ {app_dict['price'] if app_dict['price'] is not None else 0.00:.2f}".replace('.', ',')
            
            appointments_list.append(app_dict)
        
        return jsonify(appointments_list)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        conn.close()


# --- 7. AUTENTICAÇÃO E SESSÃO ---

@app.route('/api/login', methods=['POST'])
def login():
    """Rota para login do administrador."""
    data = request.json
    key = data.get('admin_key')
    
    if key == ADMIN_KEY:
        session['logged_in'] = True
        return jsonify({"message": "Login bem-sucedido"}), 200
    else:
        return jsonify({"error": "Chave de administrador inválida"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    """Rota para logout do administrador."""
    session.pop('logged_in', None)
    return jsonify({"message": "Logout bem-sucedido"}), 200

@app.route('/api/check-auth', methods=['GET'])
def check_auth():
    """Verifica o status de autenticação."""
    return jsonify({"logged_in": session.get('logged_in', False)}), 200

# --- 8. ROTA PRINCIPAL (RENDERIZAÇÃO DO HTML) ---

@app.route('/', methods=['GET'])
def index():
    """Renderiza a aplicação de página única."""
    # O HTML é injetado como uma string para ser renderizado pelo Flask.
    # TODO: Certifique-se de que o código HTML/JS/CSS seja MINIMALISTA e funcional.
    
    html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BarberFlow - Agendamento e Gestão</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            theme: {{
                extend: {{
                    colors: {{
                        'primary': '#2D3748', // Cor escura principal (Texto/Fundo)
                        'secondary': '#B87333', // Bronze/Ouro (Destaque/Botões)
                        'accent': '#4A5568', // Cinza escuro
                        'bg-light': '#F7FAFC', // Fundo claro
                    }},
                    fontFamily: {{
                        sans: ['Inter', 'sans-serif'],
                    }},
                }}
            }}
        }}
    </script>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&display=swap" rel="stylesheet">
    <style>
        body {{
            background-color: #f0f4f8; /* Fundo suave */
            background-image: linear-gradient(135deg, #f0f4f8 0%, #d8e1e9 100%);
            min-height: 100vh;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 1rem; /* Padding responsivo no body */
        }}
        /* Estilo para a barra de rolagem horizontal em tabelas (responsividade) */
        .overflow-x-auto::-webkit-scrollbar {{
            height: 8px;
        }}
        .overflow-x-auto::-webkit-scrollbar-thumb {{
            background-color: #B87333;
            border-radius: 10px;
        }}
        .overflow-x-auto::-webkit-scrollbar-track {{
            background: #f1f1f1;
        }}
        /* Estilo para inputs de data/hora para melhorar a aparência */
        input[type="date"], input[type="time"], select {{
            padding: 0.75rem;
            border-radius: 0.5rem;
            border: 1px solid #E2E8F0;
            transition: border-color 0.15s ease-in-out, box-shadow 0.15s ease-in-out;
            width: 100%;
        }}
        /* Melhoria para botões */
        .btn-primary {{
            transition: all 0.3s ease;
        }}
        .btn-primary:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1);
        }}
        /* Animação de Loading */
        .spinner {{
            border: 4px solid rgba(255, 255, 255, 0.1);
            border-left-color: #B87333;
            border-radius: 50%;
            width: 24px;
            height: 24px;
            animation: spin 1s linear infinite;
        }}
        @keyframes spin {{
            0% {{ transform: rotate(0deg); }}
            100% {{ transform: rotate(360deg); }}
        }}
    </style>
</head>
<body class="font-sans">

    <div id="loading-overlay" class="fixed inset-0 bg-primary bg-opacity-75 flex items-center justify-center z-50 hidden">
        <div class="spinner"></div>
    </div>

    <!-- Container Principal Responsivo -->
    <div id="app-container" class="w-full max-w-7xl mx-auto p-4 md:p-8 bg-white/90 shadow-2xl rounded-xl backdrop-blur-sm">

        <!-- Header -->
        <header class="text-center mb-6 md:mb-10 border-b pb-4">
            <h1 class="text-3xl md:text-5xl font-extrabold text-primary tracking-tight">Barber<span class="text-secondary">Flow</span></h1>
            <p id="app-subtitle" class="text-lg md:text-xl text-accent mt-1">Seu sistema de gestão e agendamento.</p>
        </header>
        
        <!-- Controles de Visão (Agenda/Admin) - Escondido no início -->
        <div id="view-switch-container" class="flex justify-center mb-6 space-x-4 hidden">
            <button onclick="changeView('schedule')" class="flex-1 py-2 px-4 rounded-lg bg-secondary text-white font-semibold shadow-md btn-primary hover:bg-[#a1652e] transition-colors" id="btn-schedule">
                Agendar Horário
            </button>
            <button onclick="changeView('admin')" class="flex-1 py-2 px-4 rounded-lg bg-accent text-white font-semibold shadow-md hover:bg-gray-600 transition-colors" id="btn-admin">
                Painel de Gestão
            </button>
        </div>

        <!-- Visão de Login -->
        <div id="login-view" class="w-full max-w-sm mx-auto p-6 md:p-8 space-y-6 bg-white rounded-xl shadow-lg hidden">
            <h2 class="text-2xl font-bold text-center text-primary">Login de Gestão</h2>
            <div id="role-selection" class="space-y-4">
                <button onclick="handleRoleSelection('admin')" class="w-full py-3 rounded-lg bg-secondary text-white font-semibold hover:bg-[#a1652e] btn-primary">
                    Sou o Barbeiro (Admin)
                </button>
                <button onclick="handleRoleSelection('client')" class="w-full py-3 rounded-lg bg-gray-200 text-primary font-semibold hover:bg-gray-300">
                    Sou um Cliente (Agendar)
                </button>
            </div>
            <form id="admin-login-form" class="space-y-4 hidden" onsubmit="event.preventDefault(); handleAdminLogin();">
                <input type="password" id="admin-key" placeholder="Chave de Administrador" required 
                       class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary transition-colors">
                <button type="submit" class="w-full py-3 rounded-lg bg-primary text-white font-semibold hover:bg-accent btn-primary">
                    Entrar
                </button>
                <button type="button" onclick="handleRoleSelection('client')" class="w-full py-2 text-sm text-accent hover:text-primary transition-colors">
                    Voltar para Agendamento
                </button>
            </form>
            <p id="login-message" class="text-center text-red-500 hidden"></p>
        </div>

        <!-- Visão de Agendamento (Clientes) -->
        <div id="schedule-view" class="max-w-xl mx-auto space-y-6 hidden">
            <h2 class="text-2xl font-bold text-primary text-center">Agende seu Corte</h2>
            
            <!-- Formulário de Agendamento -->
            <form id="appointment-form" class="bg-bg-light p-6 rounded-xl shadow-md space-y-4" onsubmit="event.preventDefault(); handleAppointmentSubmit();">
                
                <!-- Dados do Cliente -->
                <div class="space-y-4 border-b pb-4">
                    <input type="text" id="client-name" placeholder="Seu Nome Completo" required
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <input type="tel" id="client-phone" placeholder="Seu Telefone (Opcional)"
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <select id="service-select" required onchange="updateAvailableSlots()"
                            class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                        <option value="">Selecione um Serviço</option>
                    </select>
                </div>
                
                <!-- Seleção de Data e Hora -->
                <div class="space-y-4">
                    <label class="block text-primary font-semibold">Selecione a Data:</label>
                    <input type="date" id="appointment-date" required onchange="updateAvailableSlots()"
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                </div>

                <!-- Slots de Horário -->
                <div id="time-slots-container" class="space-y-3">
                    <label class="block text-primary font-semibold">Horários Disponíveis:</label>
                    <div id="time-slots" class="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-5 gap-3">
                        <p class="col-span-3 sm:col-span-4 md:col-span-5 text-gray-500">Selecione a data e o serviço.</p>
                    </div>
                </div>

                <!-- Botão de Envio -->
                <button type="submit" id="submit-appointment" disabled
                        class="w-full py-3 rounded-lg bg-gray-400 text-white font-bold transition-colors">
                    Confirmar Agendamento
                </button>
            </form>
            <p id="appointment-message" class="text-center font-semibold mt-4"></p>
            <button onclick="logout()" class="w-full py-2 text-sm text-accent hover:text-primary transition-colors mt-4">
                Voltar
            </button>
        </div>

        <!-- Visão de Administração (Admin) -->
        <div id="admin-view" class="hidden">
            <h2 class="text-2xl font-bold text-primary text-center mb-6">Painel de Gestão</h2>

            <!-- Tabs de Administração (Responsivo) -->
            <div class="flex flex-wrap border-b border-gray-200 mb-6">
                <button onclick="changeAdminTab('appointments')" class="tab-button flex-1 md:flex-none py-2 px-4 text-center text-primary font-semibold border-b-2 border-secondary bg-bg-light rounded-t-lg transition-colors" data-tab="appointments">
                    Agendamentos
                </button>
                <button onclick="changeAdminTab('services')" class="tab-button flex-1 md:flex-none py-2 px-4 text-center text-accent font-semibold border-b-2 border-transparent hover:border-gray-300 transition-colors" data-tab="services">
                    Serviços
                </button>
                <button onclick="changeAdminTab('expenses')" class="tab-button flex-1 md:flex-none py-2 px-4 text-center text-accent font-semibold border-b-2 border-transparent hover:border-gray-300 transition-colors" data-tab="expenses">
                    Despesas
                </button>
                <button onclick="changeAdminTab('finance')" class="tab-button flex-1 md:flex-none py-2 px-4 text-center text-accent font-semibold border-b-2 border-transparent hover:border-gray-300 transition-colors" data-tab="finance">
                    Relatório Financeiro
                </button>
            </div>

            <!-- Conteúdo da Aba de Agendamentos -->
            <div id="admin-appointments-tab" class="admin-tab-content space-y-6">
                <h3 class="text-xl font-semibold text-primary">Próximos Agendamentos (Não Arquivados)</h3>
                
                <!-- Tabela de Agendamentos (Responsivo: overflow-x-auto) -->
                <div class="overflow-x-auto bg-white rounded-lg shadow">
                    <table id="appointments-table" class="min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Data/Hora</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Cliente</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Serviço</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Valor</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Ações</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200" id="appointments-list">
                            <tr><td colspan="6" class="p-4 text-center text-gray-500">Carregando agendamentos...</td></tr>
                        </tbody>
                    </table>
                </div>

                <div class="border-t pt-4">
                    <button onclick="changeAdminTab('archived')" class="py-2 px-4 text-sm rounded-lg bg-gray-100 text-accent hover:bg-gray-200 transition-colors">
                        Ver Histórico/Arquivados
                    </button>
                </div>
            </div>

            <!-- Conteúdo da Aba de Serviços -->
            <div id="admin-services-tab" class="admin-tab-content space-y-6 hidden">
                <h3 class="text-xl font-semibold text-primary">Gerenciar Serviços</h3>
                
                <!-- Formulário de Serviços -->
                <form id="service-form" class="bg-bg-light p-6 rounded-xl shadow-md space-y-4 max-w-lg mx-auto" onsubmit="event.preventDefault(); handleServiceSubmit();">
                    <input type="hidden" id="service-id-edit">
                    <input type="text" id="service-name" placeholder="Nome do Serviço" required
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <input type="number" id="service-duration" placeholder="Duração (minutos)" required min="1" step="1"
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <input type="number" id="service-price" placeholder="Preço (R$)" required min="0.01" step="0.01"
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    
                    <div class="flex space-x-4">
                        <button type="submit" id="service-submit-btn" 
                                class="flex-1 py-3 rounded-lg bg-secondary text-white font-bold btn-primary">
                            Adicionar Serviço
                        </button>
                        <button type="button" onclick="clearServiceForm()"
                                class="flex-1 py-3 rounded-lg bg-gray-400 text-white font-bold hover:bg-gray-500 transition-colors">
                            Limpar
                        </button>
                    </div>
                </form>
                
                <!-- Tabela de Serviços (Responsivo: overflow-x-auto) -->
                <div class="overflow-x-auto bg-white rounded-lg shadow">
                    <table class="min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Serviço</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Duração (min)</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Preço</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Ações</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200" id="services-list">
                            <tr><td colspan="4" class="p-4 text-center text-gray-500">Carregando serviços...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
            
            <!-- Conteúdo da Aba de Despesas -->
            <div id="admin-expenses-tab" class="admin-tab-content space-y-6 hidden">
                <h3 class="text-xl font-semibold text-primary">Gerenciar Despesas</h3>
                
                <!-- Formulário de Despesas -->
                <form id="expense-form" class="bg-bg-light p-6 rounded-xl shadow-md space-y-4 max-w-lg mx-auto" onsubmit="event.preventDefault(); handleExpenseSubmit();">
                    <input type="text" id="expense-description" placeholder="Descrição da Despesa" required
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <input type="number" id="expense-amount" placeholder="Valor (R$)" required min="0.01" step="0.01"
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <input type="date" id="expense-date" required value="{{ date.today().isoformat() }}"
                           class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    <div class="flex items-center space-x-2">
                        <input type="checkbox" id="expense-is-fixed" 
                               class="h-4 w-4 text-secondary border-gray-300 rounded focus:ring-secondary">
                        <label for="expense-is-fixed" class="text-primary">Despesa Fixa (Recorrente)</label>
                    </div>
                    <button type="submit" class="w-full py-3 rounded-lg bg-primary text-white font-bold hover:bg-accent btn-primary">
                        Registrar Despesa
                    </button>
                </form>
                
                <!-- Tabela de Despesas (Responsivo: overflow-x-auto) -->
                <div class="overflow-x-auto bg-white rounded-lg shadow">
                    <table class="min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Data</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Descrição</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Valor</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Tipo</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Ações</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200" id="expenses-list">
                            <tr><td colspan="5" class="p-4 text-center text-gray-500">Carregando despesas...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Conteúdo da Aba Financeiro -->
            <div id="admin-finance-tab" class="admin-tab-content space-y-6 hidden">
                <h3 class="text-xl font-semibold text-primary">Relatório Financeiro</h3>
                
                <!-- Filtro de Data -->
                <div class="bg-bg-light p-6 rounded-xl shadow-md max-w-lg mx-auto space-y-4">
                    <div class="flex flex-col sm:flex-row space-y-4 sm:space-y-0 sm:space-x-4">
                        <input type="date" id="finance-start-date" required
                               class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                        <input type="date" id="finance-end-date" required
                               class="w-full p-3 border border-gray-300 rounded-lg focus:ring-secondary focus:border-secondary">
                    </div>
                    <button onclick="loadFinanceReport()" class="w-full py-3 rounded-lg bg-secondary text-white font-bold btn-primary">
                        Gerar Relatório
                    </button>
                </div>
                
                <!-- Resultados do Relatório -->
                <div id="finance-results" class="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-lg mx-auto">
                    <!-- Cards de Receita, Despesa, Lucro -->
                    <div class="p-4 bg-green-50 rounded-xl shadow-md text-center">
                        <p class="text-sm font-medium text-green-700">Receita Total</p>
                        <p id="finance-revenue" class="text-2xl font-bold text-green-900">R$ 0,00</p>
                    </div>
                    <div class="p-4 bg-red-50 rounded-xl shadow-md text-center">
                        <p class="text-sm font-medium text-red-700">Despesas Totais</p>
                        <p id="finance-expenses" class="text-2xl font-bold text-red-900">R$ 0,00</p>
                    </div>
                    <div class="p-4 bg-blue-50 rounded-xl shadow-md text-center">
                        <p class="text-sm font-medium text-blue-700">Lucro</p>
                        <p id="finance-profit" class="text-2xl font-bold text-blue-900">R$ 0,00</p>
                    </div>
                </div>
            </div>

            <!-- Conteúdo da Aba Arquivados (Histórico) -->
            <div id="admin-archived-tab" class="admin-tab-content space-y-6 hidden">
                <h3 class="text-xl font-semibold text-primary">Histórico de Agendamentos (Arquivados)</h3>
                <p class="text-sm text-gray-500">Estes são agendamentos que já foram concluídos ou cancelados e foram movidos para o histórico.</p>
                
                <!-- Tabela de Agendamentos Arquivados (Responsivo: overflow-x-auto) -->
                <div class="overflow-x-auto bg-white rounded-lg shadow">
                    <table class="min-w-full divide-y divide-gray-200">
                        <thead class="bg-gray-50">
                            <tr>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Data/Hora</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Cliente</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Serviço</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Valor</th>
                                <th class="px-3 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                            </tr>
                        </thead>
                        <tbody class="bg-white divide-y divide-gray-200" id="archived-appointments-list">
                            <tr><td colspan="5" class="p-4 text-center text-gray-500">Carregando histórico...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>

            <!-- Logout -->
            <div class="mt-8 text-center border-t pt-4">
                <button onclick="logout()" class="text-red-500 hover:text-red-700 font-semibold transition-colors">
                    Sair do Painel de Gestão
                </button>
            </div>
        </div>

    </div>

    <!-- Modal Global -->
    <div id="global-modal" class="fixed inset-0 bg-gray-900 bg-opacity-75 hidden items-center justify-center z-40">
        <div id="modal-content" class="bg-white p-6 rounded-lg shadow-2xl w-11/12 max-w-lg md:w-full space-y-4">
            <h3 id="modal-title" class="text-xl font-bold text-primary">Título</h3>
            <p id="modal-body" class="text-accent"></p>
            <div class="flex justify-end space-x-3">
                <button onclick="closeModal()" class="py-2 px-4 rounded-lg bg-gray-300 text-primary font-semibold hover:bg-gray-400 transition-colors">
                    Fechar
                </button>
                <button id="modal-action-btn" class="py-2 px-4 rounded-lg bg-secondary text-white font-semibold btn-primary hidden">
                    Ação
                </button>
            </div>
        </div>
    </div>


    <script>
        // Variáveis globais para armazenar dados e estado
        let allServices = [];
        let selectedDate = '';
        let selectedServiceDuration = 0;
        let selectedTime = '';

        // Constantes de API
        const API_BASE = '/api';

        // --- FUNÇÕES DE UTILIDADE ---

        function showLoading(show) {{
            const overlay = document.getElementById('loading-overlay');
            if (show) {{
                overlay.classList.remove('hidden');
            }} else {{
                overlay.classList.add('hidden');
            }}
        }}

        function showModal(title, body, actionText = null, actionCallback = null) {{
            document.getElementById('modal-title').innerText = title;
            document.getElementById('modal-body').innerHTML = body;
            const actionBtn = document.getElementById('modal-action-btn');
            
            if (actionText && actionCallback) {{
                actionBtn.innerText = actionText;
                actionBtn.onclick = actionCallback;
                actionBtn.classList.remove('hidden');
            }} else {{
                actionBtn.classList.add('hidden');
            }}
            
            document.getElementById('global-modal').classList.remove('hidden');
            document.getElementById('global-modal').classList.add('flex');
        }}

        function closeModal() {{
            document.getElementById('global-modal').classList.add('hidden');
            document.getElementById('global-modal').classList.remove('flex');
        }}
        
        function formatCurrency(value) {{
            // Assume value is a number or string that can be parsed as float
            const number = parseFloat(value);
            if (isNaN(number)) return 'R$ 0,00';
            return `R$ ${{number.toFixed(2)}}`.replace('.', ',');
        }}

        // --- FUNÇÕES DE NAVEGAÇÃO E VISUALIZAÇÃO ---

        function changeView(viewName) {{
            document.getElementById('schedule-view').classList.add('hidden');
            document.getElementById('admin-view').classList.add('hidden');
            document.getElementById('login-view').classList.add('hidden');
            document.getElementById('view-switch-container').classList.add('hidden');
            document.getElementById('app-subtitle').innerText = 'Seu sistema de gestão e agendamento.';

            if (viewName === 'schedule') {{
                document.getElementById('schedule-view').classList.remove('hidden');
                document.getElementById('view-switch-container').classList.remove('hidden');
                document.getElementById('btn-schedule').classList.remove('bg-accent', 'text-white', 'hover:bg-gray-600');
                document.getElementById('btn-schedule').classList.add('bg-secondary', 'text-white');
                document.getElementById('btn-admin').classList.add('bg-accent', 'text-white', 'hover:bg-gray-600');
                document.getElementById('btn-admin').classList.remove('bg-secondary', 'text-white');
                document.getElementById('app-subtitle').innerText = 'Agende seu horário com a BarberFlow.';
            }} else if (viewName === 'admin') {{
                document.getElementById('admin-view').classList.remove('hidden');
                document.getElementById('view-switch-container').classList.remove('hidden');
                document.getElementById('btn-admin').classList.remove('bg-accent', 'text-white', 'hover:bg-gray-600');
                document.getElementById('btn-admin').classList.add('bg-secondary', 'text-white');
                document.getElementById('btn-schedule').classList.add('bg-accent', 'text-white', 'hover:bg-gray-600');
                document.getElementById('btn-schedule').classList.remove('bg-secondary', 'text-white');
                document.getElementById('app-subtitle').innerText = 'Painel de Gestão e Controle.';
                // Carrega a aba padrão do admin
                changeAdminTab('appointments'); 
            }} else if (viewName === 'login') {{
                document.getElementById('login-view').classList.remove('hidden');
            }}
        }}

        function changeAdminTab(tabName) {{
            document.querySelectorAll('.admin-tab-content').forEach(tab => tab.classList.add('hidden'));
            document.getElementById(`admin-${{tabName}}-tab`).classList.remove('hidden');
            
            document.querySelectorAll('.tab-button').forEach(btn => {{
                if (btn.getAttribute('data-tab') === tabName) {{
                    btn.classList.remove('text-accent', 'border-transparent', 'hover:border-gray-300');
                    btn.classList.add('text-primary', 'border-secondary', 'bg-bg-light');
                }} else {{
                    btn.classList.remove('text-primary', 'border-secondary', 'bg-bg-light');
                    btn.classList.add('text-accent', 'border-transparent', 'hover:border-gray-300');
                }}
            }});
            
            // Ações de carregamento específicas por aba
            if (tabName === 'appointments') {{
                loadAppointments();
            }} else if (tabName === 'services') {{
                loadServices();
            }} else if (tabName === 'expenses') {{
                loadExpenses();
            }} else if (tabName === 'finance') {{
                // Configura datas padrão (Mês atual)
                const today = new Date();
                const firstDay = new Date(today.getFullYear(), today.getMonth(), 1).toISOString().split('T')[0];
                const lastDay = new Date(today.getFullYear(), today.getMonth() + 1, 0).toISOString().split('T')[0];
                document.getElementById('finance-start-date').value = firstDay;
                document.getElementById('finance-end-date').value = lastDay;
                loadFinanceReport();
            }} else if (tabName === 'archived') {{
                loadArchivedAppointments();
            }}
        }}


        // --- FUNÇÕES DE DADOS (API CALLS) ---

        async function fetchServices() {{
            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/services`);
                if (!response.ok) throw new Error('Falha ao carregar serviços');
                allServices = await response.json();
                
                // Preenche o seletor de agendamento (cliente)
                const select = document.getElementById('service-select');
                select.innerHTML = '<option value="">Selecione um Serviço</option>';
                allServices.forEach(service => {{
                    const option = document.createElement('option');
                    option.value = service.id;
                    option.setAttribute('data-duration', service.duration_minutes);
                    option.innerText = `${{service.name}} ({{service.duration_minutes}} min - R$ ${{service.price.toFixed(2).replace('.', ',')}})`;
                    select.appendChild(option);
                }});
                
                // Preenche a lista de serviços do admin (se estiver na aba)
                if (document.getElementById('admin-services-tab').classList.contains('hidden') === false) {{
                    renderAdminServices();
                }}
                
            }} catch (error) {{
                showModal('Erro', `Não foi possível carregar os serviços: ${{error.message}}`);
            }} finally {{
                showLoading(false);
            }}
        }}
        
        // --- CLIENTE: FUNÇÕES DE AGENDAMENTO ---

        async function updateAvailableSlots() {{
            selectedDate = document.getElementById('appointment-date').value;
            const serviceSelect = document.getElementById('service-select');
            const selectedOption = serviceSelect.options[serviceSelect.selectedIndex];
            
            if (selectedOption.value) {{
                selectedServiceDuration = parseInt(selectedOption.getAttribute('data-duration'), 10);
            }} else {{
                selectedServiceDuration = 0;
            }}
            
            const slotsContainer = document.getElementById('time-slots');
            const submitBtn = document.getElementById('submit-appointment');
            
            // Resetar
            slotsContainer.innerHTML = '<p class="col-span-3 sm:col-span-4 md:col-span-5 text-gray-500">Aguarde...</p>';
            submitBtn.disabled = true;
            submitBtn.classList.remove('bg-secondary', 'btn-primary');
            submitBtn.classList.add('bg-gray-400');
            selectedTime = '';
            
            if (!selectedDate || selectedServiceDuration === 0) {{
                slotsContainer.innerHTML = '<p class="col-span-3 sm:col-span-4 md:col-span-5 text-gray-500">Selecione a data e o serviço.</p>';
                return;
            }}

            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/available-slots?date=${{selectedDate}}&duration=${{selectedServiceDuration}}`);
                if (!response.ok) throw new Error('Falha ao buscar horários');
                
                const data = await response.json();
                
                slotsContainer.innerHTML = '';
                if (data.slots.length === 0) {{
                    slotsContainer.innerHTML = '<p class="col-span-3 sm:col-span-4 md:col-span-5 text-red-500 font-semibold">Nenhum horário disponível nesta data. Tente outro dia.</p>';
                }} else {{
                    data.slots.forEach(slot => {{
                        const button = document.createElement('button');
                        button.type = 'button';
                        button.innerText = slot;
                        button.className = 'slot-btn py-2 px-1 rounded-lg bg-gray-100 text-primary font-medium hover:bg-secondary hover:text-white transition-colors text-sm';
                        button.onclick = () => selectTimeSlot(slot);
                        slotsContainer.appendChild(button);
                    }});
                }}
                
            }} catch (error) {{
                slotsContainer.innerHTML = `<p class="col-span-3 sm:col-span-4 md:col-span-5 text-red-500">Erro: ${{error.message}}</p>`;
            }} finally {{
                showLoading(false);
            }}
        }}

        function selectTimeSlot(time) {{
            selectedTime = time;
            document.querySelectorAll('.slot-btn').forEach(btn => {{
                btn.classList.remove('bg-secondary', 'text-white');
                btn.classList.add('bg-gray-100', 'text-primary');
            }});
            
            const selectedBtn = Array.from(document.querySelectorAll('.slot-btn')).find(btn => btn.innerText === time);
            if (selectedBtn) {{
                selectedBtn.classList.remove('bg-gray-100', 'text-primary');
                selectedBtn.classList.add('bg-secondary', 'text-white');
            }}
            
            // Habilita o botão de submissão
            const submitBtn = document.getElementById('submit-appointment');
            submitBtn.disabled = false;
            submitBtn.classList.remove('bg-gray-400');
            submitBtn.classList.add('bg-secondary', 'btn-primary');
        }}

        async function handleAppointmentSubmit() {{
            if (!selectedTime) {{
                showModal('Atenção', 'Por favor, selecione um horário disponível.');
                return;
            }}

            const name = document.getElementById('client-name').value;
            const phone = document.getElementById('client-phone').value;
            const serviceId = document.getElementById('service-select').value;
            
            if (!name || !serviceId || !selectedDate || !selectedTime) {{
                showModal('Atenção', 'Por favor, preencha todos os campos obrigatórios (Nome, Serviço e Horário).');
                return;
            }}

            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/appointments`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        client_name: name,
                        client_phone: phone,
                        service_id: parseInt(serviceId, 10),
                        appointment_date: selectedDate,
                        appointment_time: selectedTime
                    }})
                }});
                
                const result = await response.json();

                if (response.ok) {{
                    showModal('Agendamento Confirmado!', 
                             `<p>Obrigado, ${{name}}! Seu agendamento para o serviço 
                              está confirmado para o dia <strong>${{selectedDate.split('-').reverse().join('/')}}</strong> 
                              às <strong>${{selectedTime}}</strong>.</p>
                              <p class="mt-2 text-sm text-green-700">Seu agendamento está em status 'Confirmado' (administrador será notificado).</p>`);
                    // Limpar formulário e slots
                    document.getElementById('appointment-form').reset();
                    document.getElementById('time-slots').innerHTML = '<p class="col-span-3 sm:col-span-4 md:col-span-5 text-gray-500">Selecione a data e o serviço.</p>';
                    document.getElementById('submit-appointment').disabled = true;
                    document.getElementById('submit-appointment').classList.remove('bg-secondary', 'btn-primary');
                    document.getElementById('submit-appointment').classList.add('bg-gray-400');
                    selectedTime = '';
                }} else if (response.status === 409) {{
                    // Conflito de horário
                     showModal('Horário Indisponível', 
                             `<p>${{result.error || 'O horário selecionado foi reservado por outro cliente. Por favor, selecione um novo horário.'}}</p>`);
                    // Atualiza os slots para o usuário tentar novamente
                    updateAvailableSlots();
                }} else {{
                    throw new Error(result.error || 'Erro desconhecido ao agendar.');
                }}
                
            }} catch (error) {{
                showModal('Erro no Agendamento', `Ocorreu um erro: ${{error.message}}`);
            }} finally {{
                showLoading(false);
            }}
        }}
        
        // --- ADMIN: FUNÇÕES DE AGENDAMENTOS ---

        async function loadAppointments() {{
            const list = document.getElementById('appointments-list');
            list.innerHTML = '<tr><td colspan="6" class="p-4 text-center text-gray-500">Carregando agendamentos...</td></tr>';
            showLoading(true);
            
            try {{
                const response = await fetch(`${{API_BASE}}/admin/appointments`);
                if (response.status === 401) throw new Error('Não autorizado. Redirecionando para login.');
                if (!response.ok) throw new Error('Falha ao carregar agendamentos.');
                
                const appointments = await response.json();
                
                list.innerHTML = '';
                if (appointments.length === 0) {{
                    list.innerHTML = '<tr><td colspan="6" class="p-4 text-center text-gray-500">Nenhum agendamento pendente.</td></tr>';
                }} else {{
                    appointments.forEach(app => {{
                        const row = document.createElement('tr');
                        row.className = 'hover:bg-gray-50';
                        
                        let statusColor = '';
                        let statusText = app.status;
                        if (app.status === 'Pendente') {{
                            statusColor = 'bg-yellow-100 text-yellow-800';
                        }} else if (app.status === 'Confirmado') {{
                            statusColor = 'bg-blue-100 text-blue-800';
                        }} else if (app.status === 'Concluído') {{
                            statusColor = 'bg-green-100 text-green-800';
                        }} else if (app.status === 'Cancelado') {{
                            statusColor = 'bg-red-100 text-red-800';
                        }}
                        
                        row.innerHTML = `
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.appointment_date_formatted}}<br><span class="font-bold">${{app.appointment_time.substring(0, 5)}}</span></td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.client_name}} (${{app.client_phone || 'N/A'}})</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.service_name}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900 font-semibold">${{app.price_formatted}}</td>
                            <td class="px-3 py-3 whitespace-nowrap">
                                <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${{statusColor}}">
                                    ${{statusText}}
                                </span>
                            </td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm font-medium">
                                <select onchange="updateAppointmentStatus(${{app.id}}, this.value)" class="p-1 border rounded text-xs">
                                    <option value="Pendente" ${{app.status === 'Pendente' ? 'selected' : ''}}>Pendente</option>
                                    <option value="Confirmado" ${{app.status === 'Confirmado' ? 'selected' : ''}}>Confirmado</option>
                                    <option value="Concluído" ${{app.status === 'Concluído' ? 'selected' : ''}}>Concluído</option>
                                    <option value="Cancelado" ${{app.status === 'Cancelado' ? 'selected' : ''}}>Cancelado</option>
                                </select>
                                <button onclick="archiveAppointment(${{app.id}})" class="ml-2 text-gray-400 hover:text-gray-600 transition-colors text-sm" title="Arquivar/Mover para Histórico">
                                    &#x1F5C3; <!-- Ícone de Pasta (Arquivar) -->
                                </button>
                            </td>
                        `;
                        list.appendChild(row);
                    }});
                }}
            }} catch (error) {{
                list.innerHTML = `<tr><td colspan="6" class="p-4 text-center text-red-500">Erro: ${{error.message}}</td></tr>`;
                if (error.message.includes('Não autorizado')) {{
                    checkAuthAndRedirect();
                }}
            }} finally {{
                showLoading(false);
            }}
        }}

        function updateAppointmentStatus(appId, newStatus) {{
            showModal('Confirmar Ação', `Deseja realmente alterar o status do agendamento #${{appId}} para <strong>${{newStatus}}</strong>?`, 
                      'Confirmar', async () => {{
                closeModal();
                showLoading(true);
                try {{
                    const response = await fetch(`${{API_BASE}}/admin/appointments/${{appId}}`, {{
                        method: 'PUT',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{ status: newStatus }})
                    }});
                    
                    if (!response.ok) throw new Error('Falha ao atualizar status.');
                    
                    // Recarrega a lista para mostrar a atualização
                    loadAppointments();
                }} catch (error) {{
                    showModal('Erro', `Não foi possível atualizar o status: ${{error.message}}`);
                }} finally {{
                    showLoading(false);
                }}
            }});
        }}

        function archiveAppointment(appId) {{
            showModal('Arquivar Agendamento', 
                      `Deseja mover o agendamento #${{appId}} para o histórico (Arquivar)?<br>
                       Ele não aparecerá mais na lista principal, mas estará no Relatório Financeiro se status for 'Concluído'.`, 
                      'Arquivar', async () => {{
                closeModal();
                showLoading(true);
                try {{
                    const response = await fetch(`${{API_BASE}}/admin/archive/${{appId}}`, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }}
                    }});
                    
                    if (!response.ok) throw new Error('Falha ao arquivar agendamento.');
                    
                    loadAppointments(); // Recarrega a lista principal
                }} catch (error) {{
                    showModal('Erro', `Não foi possível arquivar: ${{error.message}}`);
                }} finally {{
                    showLoading(false);
                }}
            }});
        }}

        async function loadArchivedAppointments() {{
            const list = document.getElementById('archived-appointments-list');
            list.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-gray-500">Carregando histórico...</td></tr>';
            showLoading(true);
            
            try {{
                const response = await fetch(`${{API_BASE}}/admin/archived-appointments`);
                if (!response.ok) throw new Error('Falha ao carregar histórico.');
                
                const appointments = await response.json();
                
                list.innerHTML = '';
                if (appointments.length === 0) {{
                    list.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-gray-500">Nenhum agendamento arquivado.</td></tr>';
                }} else {{
                    appointments.forEach(app => {{
                        const row = document.createElement('tr');
                        row.className = 'hover:bg-gray-50';
                        
                        let statusColor = 'bg-gray-100 text-gray-800';
                        if (app.status === 'Concluído') {{
                            statusColor = 'bg-green-100 text-green-800';
                        }} else if (app.status === 'Cancelado') {{
                            statusColor = 'bg-red-100 text-red-800';
                        }}
                        
                        row.innerHTML = `
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.appointment_date_formatted}}<br><span class="font-bold">${{app.appointment_time.substring(0, 5)}}</span></td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.client_name}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.service_name || 'Serviço Deletado'}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-900">${{app.price_formatted}}</td>
                            <td class="px-3 py-3 whitespace-nowrap">
                                <span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${{statusColor}}">
                                    ${{app.status}}
                                </span>
                            </td>
                        `;
                        list.appendChild(row);
                    }});
                }}
            }} catch (error) {{
                list.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-red-500">Erro ao carregar histórico: ${{error.message}}</td></tr>`;
            }} finally {{
                showLoading(false);
            }}
        }}

        // --- ADMIN: FUNÇÕES DE SERVIÇOS ---

        function renderAdminServices() {{
            const list = document.getElementById('services-list');
            list.innerHTML = '';
            
            if (allServices.length === 0) {{
                list.innerHTML = '<tr><td colspan="4" class="p-4 text-center text-gray-500">Nenhum serviço cadastrado.</td></tr>';
                return;
            }}

            allServices.forEach(service => {{
                const row = document.createElement('tr');
                row.className = 'hover:bg-gray-50';
                
                row.innerHTML = `
                    <td class="px-3 py-3 whitespace-nowrap text-sm font-medium text-gray-900">${{service.name}}</td>
                    <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-500">${{service.duration_minutes}}</td>
                    <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-500">${{formatCurrency(service.price)}}</td>
                    <td class="px-3 py-3 whitespace-nowrap text-sm font-medium space-x-2">
                        <button onclick="editService(${{service.id}})" class="text-blue-600 hover:text-blue-900 transition-colors text-sm" title="Editar">&#9998;</button>
                        <button onclick="deleteService(${{service.id}})" class="text-red-600 hover:text-red-900 transition-colors text-sm" title="Excluir">&#10005;</button>
                    </td>
                `;
                list.appendChild(row);
            }});
        }}

        async function handleServiceSubmit() {{
            const id = document.getElementById('service-id-edit').value;
            const name = document.getElementById('service-name').value;
            const duration = parseInt(document.getElementById('service-duration').value, 10);
            const price = parseFloat(document.getElementById('service-price').value);
            
            if (!name || isNaN(duration) || isNaN(price) || duration <= 0 || price <= 0) {{
                showModal('Atenção', 'Por favor, preencha todos os campos do serviço corretamente.');
                return;
            }}

            showLoading(true);
            try {{
                const method = id ? 'PUT' : 'POST';
                const url = id ? `${{API_BASE}}/admin/services/${{id}}` : `${{API_BASE}}/admin/services`;
                
                const response = await fetch(url, {{
                    method: method,
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name, duration_minutes: duration, price }})
                }});
                
                const result = await response.json();

                if (response.ok) {{
                    showModal('Sucesso', result.message);
                    clearServiceForm();
                    fetchServices(); // Recarrega a lista de serviços
                }} else {{
                    throw new Error(result.error || `Erro ao ${{id ? 'atualizar' : 'adicionar'}} serviço.`);
                }}
                
            }} catch (error) {{
                showModal('Erro', `Ocorreu um erro: ${{error.message}}`);
            }} finally {{
                showLoading(false);
            }}
        }}

        function clearServiceForm() {{
            document.getElementById('service-form').reset();
            document.getElementById('service-id-edit').value = '';
            document.getElementById('service-submit-btn').innerText = 'Adicionar Serviço';
            document.getElementById('service-submit-btn').classList.remove('bg-blue-600');
            document.getElementById('service-submit-btn').classList.add('bg-secondary');
        }}

        function editService(id) {{
            const service = allServices.find(s => s.id === id);
            if (service) {{
                document.getElementById('service-id-edit').value = service.id;
                document.getElementById('service-name').value = service.name;
                document.getElementById('service-duration').value = service.duration_minutes;
                document.getElementById('service-price').value = service.price;
                
                document.getElementById('service-submit-btn').innerText = 'Atualizar Serviço';
                document.getElementById('service-submit-btn').classList.remove('bg-secondary');
                document.getElementById('service-submit-btn').classList.add('bg-blue-600');
                
                changeAdminTab('services'); // Garante que a aba de serviços esteja visível
                document.getElementById('service-name').focus();
            }}
        }}

        function deleteService(id) {{
            showModal('Confirmar Exclusão', 
                      'Tem certeza que deseja EXCLUIR este serviço? Agendamentos futuros associados terão o serviço removido.', 
                      'Excluir', async () => {{
                closeModal();
                showLoading(true);
                try {{
                    const response = await fetch(`${{API_BASE}}/admin/services/${{id}}`, {{
                        method: 'DELETE'
                    }});
                    
                    if (!response.ok) throw new Error('Falha ao excluir serviço.');
                    
                    showModal('Sucesso', 'Serviço excluído com sucesso!');
                    fetchServices(); 
                }} catch (error) {{
                    showModal('Erro', `Não foi possível excluir: ${{error.message}}`);
                }} finally {{
                    showLoading(false);
                }}
            }});
        }}

        // --- ADMIN: FUNÇÕES DE DESPESAS ---

        async function loadExpenses() {{
            const list = document.getElementById('expenses-list');
            list.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-gray-500">Carregando despesas...</td></tr>';
            showLoading(true);
            
            try {{
                const response = await fetch(`${{API_BASE}}/admin/expenses`);
                if (!response.ok) throw new Error('Falha ao carregar despesas.');
                
                const expenses = await response.json();
                
                list.innerHTML = '';
                if (expenses.length === 0) {{
                    list.innerHTML = '<tr><td colspan="5" class="p-4 text-center text-gray-500">Nenhuma despesa registrada.</td></tr>';
                }} else {{
                    expenses.forEach(exp => {{
                        const row = document.createElement('tr');
                        row.className = 'hover:bg-gray-50';
                        
                        const isFixedTag = exp.is_fixed ? '<span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-blue-100 text-blue-800">Fixa</span>' : '<span class="px-2 inline-flex text-xs leading-5 font-semibold rounded-full bg-purple-100 text-purple-800">Variável</span>';
                        
                        row.innerHTML = `
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-gray-500">${{exp.expense_date_formatted}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm font-medium text-gray-900">${{exp.description}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm text-red-600 font-semibold">${{exp.amount_formatted}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm">${{isFixedTag}}</td>
                            <td class="px-3 py-3 whitespace-nowrap text-sm font-medium">
                                <button onclick="deleteExpense(${{exp.id}})" class="text-red-600 hover:text-red-900 transition-colors text-sm" title="Excluir">&#10005;</button>
                            </td>
                        `;
                        list.appendChild(row);
                    }});
                }}
            }} catch (error) {{
                list.innerHTML = `<tr><td colspan="5" class="p-4 text-center text-red-500">Erro: ${{error.message}}</td></tr>`;
            }} finally {{
                showLoading(false);
            }}
        }}

        async function handleExpenseSubmit() {{
            const description = document.getElementById('expense-description').value;
            const amount = parseFloat(document.getElementById('expense-amount').value);
            const isFixed = document.getElementById('expense-is-fixed').checked;
            const expenseDate = document.getElementById('expense-date').value;
            
            if (!description || isNaN(amount) || amount <= 0 || !expenseDate) {{
                showModal('Atenção', 'Por favor, preencha a descrição, o valor (positivo) e a data da despesa.');
                return;
            }}

            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/admin/expenses`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ description, amount, is_fixed: isFixed, expense_date: expenseDate }})
                }});
                
                const result = await response.json();

                if (response.ok) {{
                    showModal('Sucesso', 'Despesa registrada com sucesso!');
                    document.getElementById('expense-form').reset();
                    // Garante que a data volte para hoje após o reset
                    document.getElementById('expense-date').value = new Date().toISOString().split('T')[0];
                    loadExpenses(); 
                }} else {{
                    throw new Error(result.error || 'Erro ao registrar despesa.');
                }}
                
            }} catch (error) {{
                showModal('Erro', `Ocorreu um erro: ${{error.message}}`);
            }} finally {{
                showLoading(false);
            }}
        }}

        function deleteExpense(id) {{
            showModal('Confirmar Exclusão', 
                      `Tem certeza que deseja EXCLUIR a despesa #${{id}}?`, 
                      'Excluir', async () => {{
                closeModal();
                showLoading(true);
                try {{
                    const response = await fetch(`${{API_BASE}}/admin/expenses?id=${{id}}`, {{
                        method: 'DELETE'
                    }});
                    
                    if (!response.ok) throw new Error('Falha ao excluir despesa.');
                    
                    showModal('Sucesso', 'Despesa excluída com sucesso!');
                    loadExpenses(); 
                }} catch (error) {{
                    showModal('Erro', `Não foi possível excluir: ${{error.message}}`);
                }} finally {{
                    showLoading(false);
                }}
            }});
        }}

        // --- ADMIN: FUNÇÕES FINANCEIRAS ---
        
        async function loadFinanceReport() {{
            const start_date = document.getElementById('finance-start-date').value;
            const end_date = document.getElementById('finance-end-date').value;

            if (!start_date || !end_date) {{
                showModal('Atenção', 'Por favor, selecione as datas de início e fim.');
                return;
            }}
            
            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/admin/finance?start_date=${{start_date}}&end_date=${{end_date}}`);
                if (!response.ok) throw new Error('Falha ao gerar relatório.');
                
                const report = await response.json();
                
                document.getElementById('finance-revenue').innerText = report.revenue;
                document.getElementById('finance-expenses').innerText = report.expenses;
                
                const profitElement = document.getElementById('finance-profit');
                profitElement.innerText = report.profit;
                
                // Mudar cor do lucro (verde para positivo, vermelho para negativo)
                profitElement.classList.remove('text-green-900', 'text-red-900');
                if (parseFloat(report.profit.replace('R$ ', '').replace(',', '.')) >= 0) {{
                    profitElement.classList.add('text-green-900');
                }} else {{
                    profitElement.classList.add('text-red-900');
                }}
                
            }} catch (error) {{
                showModal('Erro', `Não foi possível carregar o relatório: ${{error.message}}`);
            }} finally {{
                showLoading(false);
            }}
        }}

        // --- FUNÇÕES DE AUTENTICAÇÃO ---
        
        function handleRoleSelection(role) {{
            document.getElementById('login-message').classList.add('hidden');
            if (role === 'admin') {{
                document.getElementById('role-selection').classList.add('hidden');
                document.getElementById('admin-login-form').classList.remove('hidden');
                document.getElementById('admin-key').focus();
            }} else {{ // Cliente/Agendamento
                // Simula o sucesso do login para o cliente e move para a tela de agendamento
                document.getElementById('login-view').classList.add('hidden');
                document.getElementById('view-switch-container').classList.remove('hidden');
                changeView('schedule');
            }}
        }}

        async function handleAdminLogin() {{
            const adminKey = document.getElementById('admin-key').value;
            const loginMessage = document.getElementById('login-message');
            loginMessage.classList.add('hidden');

            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/login`, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ admin_key: adminKey }})
                }});
                
                const result = await response.json();

                if (response.ok) {{
                    document.getElementById('login-view').classList.add('hidden');
                    document.getElementById('view-switch-container').classList.remove('hidden');
                    changeView('admin'); // Vai para o painel do admin
                }} else {{
                    loginMessage.innerText = result.error || 'Chave inválida.';
                    loginMessage.classList.remove('hidden');
                }}
            }} catch (error) {{
                loginMessage.innerText = 'Erro de rede ou servidor.';
                loginMessage.classList.remove('hidden');
            }} finally {{
                showLoading(false);
            }}
        }}

        async function logout() {{
            showLoading(true);
            try {{
                await fetch(`${{API_BASE}}/logout`, {{ method: 'POST' }});
                
                // Reseta a visualização para a tela de login
                document.getElementById('schedule-view').classList.add('hidden');
                document.getElementById('admin-view').classList.add('hidden');
                document.getElementById('view-switch-container').classList.add('hidden');
                
                // Reseta o formulário de login para seleção de função
                document.getElementById('admin-login-form').classList.add('hidden');
                document.getElementById('role-selection').classList.remove('hidden');
                document.getElementById('admin-key').value = '';

                changeView('login');
                
            }} catch (error) {{
                // Se o logout falhar, ainda reseta a UI
            }} finally {{
                showLoading(false);
            }}
        }}
        
        async function checkAuthAndRedirect() {{
            showLoading(true);
            try {{
                const response = await fetch(`${{API_BASE}}/check-auth`);
                const result = await response.json();
                
                document.getElementById('login-view').classList.add('hidden');
                document.getElementById('schedule-view').classList.add('hidden');
                document.getElementById('admin-view').classList.add('hidden');

                if (result.logged_in) {{
                    document.getElementById('view-switch-container').classList.remove('hidden');
                    changeView('admin'); // Redireciona para o admin se já logado
                }} else {{
                    // Se não estiver logado, assume a tela de login/seleção de papel
                    document.getElementById('admin-login-form').classList.add('hidden');
                    document.getElementById('role-selection').classList.remove('hidden');
                    document.getElementById('login-view').classList.remove('hidden');
                }}
                
            }} catch (e) {{
                document.getElementById('login-view').classList.remove('hidden');
            }} finally {{
                showLoading(false);
            }}
        }}

        // --- INICIALIZAÇÃO ---

        document.addEventListener('DOMContentLoaded', () => {{
            // Define a data mínima para hoje
            document.getElementById('appointment-date').min = new Date().toISOString().split('T')[0];
            // Define a data de despesa para hoje
            document.getElementById('expense-date').value = new Date().toISOString().split('T')[0];
            
            // Carrega serviços e verifica autenticação
            fetchServices();
            checkAuthAndRedirect();
        }});


        // --- EXPOSIÇÃO GLOBAL DE FUNÇÕES ---
        window.changeView = changeView;
        window.handleAppointmentSubmit = handleAppointmentSubmit;
        window.changeAdminTab = changeAdminTab;
        window.closeModal = closeModal;
        window.updateAppointmentStatus = updateAppointmentStatus;
        window.handleServiceSubmit = handleServiceSubmit;
        window.clearServiceForm = clearServiceForm;
        window.editService = editService;
        window.deleteService = deleteService;
        
        // Expondo funções de despesa e arquivamento
        window.handleExpenseSubmit = handleExpenseSubmit;
        window.deleteExpense = deleteExpense;
        window.archiveAppointment = archiveAppointment;
        window.loadArchivedAppointments = loadArchivedAppointments;
        window.loadFinanceReport = loadFinanceReport; // expor para o botão de relatório
        
        window.handleRoleSelection = handleRoleSelection;
        window.handleAdminLogin = handleAdminLogin;
        window.logout = logout;
        window.updateAvailableSlots = updateAvailableSlots;
        window.selectTimeSlot = selectTimeSlot; // para uso interno no JS

    </script>
</body>
</html>
"""

    return render_template_string(html_content, date=date)

# Se o script for executado diretamente, inicie o Flask
if __name__ == '__main__':
    app.run(debug=True)
