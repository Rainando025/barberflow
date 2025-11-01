# -*- coding: utf-8 -*-
import os
import psycopg2
import psycopg2.extras 
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from datetime import datetime, date, timedelta
import calendar

# --- 1. CONFIGURAÇÃO E CONEXÃO COM POSTGRESQL ---
# ATENÇÃO: Substitua estas variáveis pelas suas credenciais reais do PostgreSQL.
DB_CONFIG = {
    'database': os.environ.get('PG_DB', 'barberflow_db'),
    'user': os.environ.get('PG_USER', 'postgres'),
    'password': os.environ.get('PG_PASSWORD', ''),
    'host': os.environ.get('PG_HOST', 'localhost'),
    'port': os.environ.get('PG_PORT', '5433')
}

# Chave secreta para sessões do Flask. MUDE ESTA CHAVE em produção!
FLASK_SECRET_KEY = 'e205e9ea1d4aaf49f7b810ef5666d7aaffad3a9f1c66dbe4763e03faffef7b90'
ADMIN_KEY = 'barberflowadmin'
FIXED_EXPENSES = 1500.00 # Exemplo de despesa fixa mensal

# --- Configurações de Horário para Agendamento ---
# Horários de funcionamento (usados para calcular slots disponíveis)
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

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY


# --- 2. FUNÇÕES DE BANCO DE DADOS (DB UTILS) ---

def execute_query(query, params=None, fetch=False):
    """Executa uma query no DB."""
    conn = get_db_connection()
    if conn is None:
        return None

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute(query, params)
            if fetch:
                if cur.rowcount > 0:
                    return cur.fetchall()
                return []
            conn.commit()
            return True
    except Exception as e:
        print(f"Erro na execução da query: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_services():
    """Busca todos os serviços disponíveis."""
    query = "SELECT id, name, price, duration_minutes FROM services ORDER BY name"
    return execute_query(query, fetch=True)

def get_appointments(date_str, include_archived=False):
    """Busca agendamentos para uma data específica. Opcionalmente inclui arquivados."""
    if include_archived:
        query = """
            SELECT a.id, a.client_name, a.client_phone, a.appointment_datetime, a.status, s.name as service_name, a.archived
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE DATE(a.appointment_datetime) = %s
            ORDER BY a.appointment_datetime
        """
    else:
        query = """
            SELECT a.id, a.client_name, a.client_phone, a.appointment_datetime, a.status, s.name as service_name, a.archived
            FROM appointments a
            JOIN services s ON a.service_id = s.id
            WHERE DATE(a.appointment_datetime) = %s AND a.archived = FALSE
            ORDER BY a.appointment_datetime
        """
    return execute_query(query, (date_str,), fetch=True)

def get_expenses(month, year):
    """Busca despesas para um mês/ano específico."""
    query = "SELECT id, description, amount, expense_date FROM expenses WHERE EXTRACT(MONTH FROM expense_date) = %s AND EXTRACT(YEAR FROM expense_date) = %s ORDER BY expense_date DESC"
    return execute_query(query, (month, year), fetch=True)

def calculate_monthly_summary(month, year):
    """Calcula o resumo financeiro do mês."""
    
    # 1. Total de Receita (apenas agendamentos concluídos, não arquivados)
    revenue_query = """
        SELECT SUM(s.price) 
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        WHERE EXTRACT(MONTH FROM a.appointment_datetime) = %s 
          AND EXTRACT(YEAR FROM a.appointment_datetime) = %s
          AND a.status = 'Concluído'
          AND a.archived = FALSE
    """
    revenue_result = execute_query(revenue_query, (month, year), fetch=True)
    total_revenue = float(revenue_result[0][0]) if revenue_result and revenue_result[0][0] else 0.0

    # 2. Total de Despesas (variáveis)
    expenses_result = get_expenses(month, year)
    total_variable_expense = sum(float(e['amount']) for e in expenses_result)

    # 3. Total de Despesas (fixas + variáveis)
    total_expense = FIXED_EXPENSES + total_variable_expense

    # 4. Lucro Líquido
    net_profit = total_revenue - total_expense

    return {
        'total_revenue': total_revenue,
        'total_expense': total_expense,
        'total_variable_expense': total_variable_expense,
        'fixed_expense': FIXED_EXPENSES,
        'net_profit': net_profit
    }

def get_appointment_slots(day):
    """Calcula os slots disponíveis para um dia específico."""
    
    # Busca agendamentos ocupados no dia (ativos e confirmados)
    occupied_slots_query = """
        SELECT appointment_datetime, s.duration_minutes
        FROM appointments a
        JOIN services s ON a.service_id = s.id
        WHERE DATE(a.appointment_datetime) = %s AND a.status IN ('Agendado', 'Confirmado') AND a.archived = FALSE
    """
    occupied_slots_data = execute_query(occupied_slots_query, (day.isoformat(),), fetch=True)
    
    occupied_ranges = []
    if occupied_slots_data:
        for slot in occupied_slots_data:
            start_time = slot['appointment_datetime']
            duration = slot['duration_minutes']
            end_time = start_time + timedelta(minutes=duration)
            occupied_ranges.append((start_time.time(), end_time.time()))

    all_slots = []
    
    # Para cada bloco de horário de funcionamento (SHOP_HOURS)
    for start_hour_str, end_hour_str in SHOP_HOURS:
        start_time = datetime.strptime(start_hour_str, '%H:%M').time()
        end_time = datetime.strptime(end_hour_str, '%H:%M').time()
        
        current_dt = datetime.combine(day, start_time)
        end_dt = datetime.combine(day, end_time)

        while current_dt < end_dt:
            slot_start_time = current_dt.time()
            slot_end_dt = current_dt + timedelta(minutes=SLOT_INTERVAL_MINUTES)
            slot_end_time = slot_end_dt.time()
            
            # Checa se o slot está no passado (apenas para o dia de hoje)
            is_past = (day == date.today() and slot_start_time <= datetime.now().time())

            is_available = True
            if not is_past:
                # Checa conflito com slots ocupados
                for occ_start, occ_end in occupied_ranges:
                    # Um slot está ocupado se o INÍCIO dele estiver dentro de um range ocupado,
                    # ou se o range ocupado se estende *sobre* o slot.
                    # Simplificação: Checa se o horário de início do slot está dentro de qualquer ocupação.
                    if occ_start <= slot_start_time < occ_end:
                        is_available = False
                        break

            # Adiciona apenas se não for passado ou se for disponível (e não passado)
            if not is_past and is_available:
                all_slots.append(slot_start_time.strftime('%H:%M'))

            current_dt = slot_end_dt

    return all_slots

# --- 3. ROTAS DE API (JSON) ---

@app.route('/api/services', methods=['GET'])
def api_get_services():
    """API: Retorna a lista de serviços."""
    services = get_services()
    if services is not None:
        # Converte para lista de dicts padrão para serialização JSON
        return jsonify([dict(row) for row in services])
    return jsonify({"error": "Erro ao buscar serviços"}), 500

@app.route('/api/slots/<date_str>', methods=['GET'])
def api_get_slots(date_str):
    """API: Retorna os slots disponíveis para a data."""
    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        if target_date < date.today():
             return jsonify([]) # Não permite agendamento no passado
        
        slots = get_appointment_slots(target_date)
        return jsonify(slots)
    except ValueError:
        return jsonify({"error": "Formato de data inválido. Use YYYY-MM-DD"}), 400

@app.route('/api/appointments/<date_str>', methods=['GET'])
def api_get_appointments(date_str):
    """API: Retorna agendamentos para a data."""
    is_admin = session.get('is_admin', False)
    if not is_admin:
        return jsonify({"error": "Não autorizado"}), 403

    try:
        # O admin pode ver os arquivados separadamente, mas por padrão na schedule (agenda) não mostramos.
        include_archived = request.args.get('include_archived', 'false').lower() == 'true'
        appointments = get_appointments(date_str, include_archived=include_archived)
        
        if appointments is not None:
            formatted_appointments = []
            for app in appointments:
                # Converte para dict padrão e formata o datetime
                formatted_app = dict(app)
                formatted_app['appointment_time'] = formatted_app['appointment_datetime'].strftime('%H:%M')
                formatted_app['appointment_date'] = formatted_app['appointment_datetime'].strftime('%Y-%m-%d')
                del formatted_app['appointment_datetime']
                formatted_appointments.append(formatted_app)
            return jsonify(formatted_appointments)
        return jsonify({"error": "Erro ao buscar agendamentos"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/appointments', methods=['POST'])
def api_schedule_appointment():
    """API: Cria um novo agendamento."""
    data = request.json
    client_name = data.get('client_name')
    client_phone = data.get('client_phone')
    service_id = data.get('service_id')
    appointment_date = data.get('date')
    appointment_time = data.get('time')
    
    if not all([client_name, client_phone, service_id, appointment_date, appointment_time]):
        return jsonify({"error": "Dados incompletos"}), 400

    try:
        # Concatena data e hora para formar um datetime
        dt_str = f"{appointment_date} {appointment_time}"
        appointment_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M')
        
        # Garante que não está agendando no passado
        if appointment_dt < datetime.now().replace(second=0, microsecond=0):
            return jsonify({"error": "Não é possível agendar no passado"}), 400

        # Checa a disponibilidade do slot
        available_slots = get_appointment_slots(appointment_dt.date())
        if appointment_time not in available_slots:
            return jsonify({"error": "Horário indisponível. Por favor, selecione outro."}), 409 # Conflict

        query = "INSERT INTO appointments (client_name, client_phone, service_id, appointment_datetime, status) VALUES (%s, %s, %s, %s, 'Agendado') RETURNING id"
        result = execute_query(query, (client_name, client_phone, service_id, appointment_dt))

        if result:
            return jsonify({"message": "Agendamento criado com sucesso!"}), 201
        return jsonify({"error": "Erro ao salvar agendamento"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/appointments/<int:app_id>/status', methods=['PUT'])
def api_update_appointment_status(app_id):
    """API: Atualiza o status de um agendamento."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403
    
    data = request.json
    new_status = data.get('status')
    
    if new_status not in ['Agendado', 'Confirmado', 'Cancelado', 'Concluído']:
        return jsonify({"error": "Status inválido"}), 400

    query = "UPDATE appointments SET status = %s WHERE id = %s"
    if execute_query(query, (new_status, app_id)):
        return jsonify({"message": "Status atualizado com sucesso"}), 200
    return jsonify({"error": "Erro ao atualizar status"}), 500

@app.route('/api/appointments/<int:app_id>/archive', methods=['PUT'])
def api_archive_appointment(app_id):
    """API: Arquiva um agendamento."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403

    query = "UPDATE appointments SET archived = TRUE WHERE id = %s"
    if execute_query(query, (app_id,)):
        return jsonify({"message": "Agendamento arquivado com sucesso"}), 200
    return jsonify({"error": "Erro ao arquivar agendamento"}), 500

@app.route('/api/admin/services', methods=['POST', 'PUT'])
def api_manage_service():
    """API: Adiciona ou atualiza um serviço."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403

    data = request.json
    name = data.get('name')
    price = data.get('price')
    duration_minutes = data.get('duration_minutes')
    service_id = data.get('id')

    if not all([name, price, duration_minutes]):
        return jsonify({"error": "Dados de serviço incompletos"}), 400

    try:
        if request.method == 'POST':
            query = "INSERT INTO services (name, price, duration_minutes) VALUES (%s, %s, %s)"
            if execute_query(query, (name, price, duration_minutes)):
                return jsonify({"message": "Serviço adicionado com sucesso"}), 201
        elif request.method == 'PUT' and service_id:
            query = "UPDATE services SET name = %s, price = %s, duration_minutes = %s WHERE id = %s"
            if execute_query(query, (name, price, duration_minutes, service_id)):
                return jsonify({"message": "Serviço atualizado com sucesso"}), 200
        
        return jsonify({"error": "Erro ao gerenciar serviço"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/services/<int:service_id>', methods=['DELETE'])
def api_delete_service(service_id):
    """API: Deleta um serviço."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403
    
    # Prevenção: não deletar serviços que possuem agendamentos
    check_query = "SELECT COUNT(*) FROM appointments WHERE service_id = %s AND archived = FALSE"
    count_result = execute_query(check_query, (service_id,), fetch=True)
    if count_result and count_result[0][0] > 0:
        return jsonify({"error": "Não é possível deletar. Existem agendamentos ativos vinculados a este serviço."}), 409 # Conflict

    query = "DELETE FROM services WHERE id = %s"
    if execute_query(query, (service_id,)):
        return jsonify({"message": "Serviço deletado com sucesso"}), 200
    return jsonify({"error": "Erro ao deletar serviço"}), 500

@app.route('/api/admin/expenses', methods=['POST'])
def api_add_expense():
    """API: Adiciona uma despesa variável."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403
    
    data = request.json
    description = data.get('description')
    amount = data.get('amount')
    expense_date_str = data.get('expense_date')

    if not all([description, amount, expense_date_str]):
        return jsonify({"error": "Dados de despesa incompletos"}), 400
    
    try:
        amount = float(amount)
        expense_date = datetime.strptime(expense_date_str, '%Y-%m-%d').date()
        query = "INSERT INTO expenses (description, amount, expense_date) VALUES (%s, %s, %s)"
        if execute_query(query, (description, amount, expense_date)):
            return jsonify({"message": "Despesa adicionada com sucesso"}), 201
        return jsonify({"error": "Erro ao adicionar despesa"}), 500
    except ValueError:
        return jsonify({"error": "Valor ou data inválida"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/admin/expenses/<int:expense_id>', methods=['DELETE'])
def api_delete_expense(expense_id):
    """API: Deleta uma despesa."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403

    query = "DELETE FROM expenses WHERE id = %s"
    if execute_query(query, (expense_id,)):
        return jsonify({"message": "Despesa deletada com sucesso"}), 200
    return jsonify({"error": "Erro ao deletar despesa"}), 500

@app.route('/api/admin/summary/<int:month>/<int:year>', methods=['GET'])
def api_get_summary(month, year):
    """API: Retorna o resumo financeiro mensal."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403

    if not 1 <= month <= 12 or not 2000 <= year <= 2100:
        return jsonify({"error": "Mês ou ano inválido"}), 400

    summary = calculate_monthly_summary(month, year)
    return jsonify(summary)

@app.route('/api/admin/expenses/<int:month>/<int:year>', methods=['GET'])
def api_get_expenses(month, year):
    """API: Retorna a lista de despesas variáveis para um mês/ano."""
    if not session.get('is_admin'):
        return jsonify({"error": "Não autorizado"}), 403
        
    expenses = get_expenses(month, year)
    if expenses is not None:
        formatted_expenses = []
        for exp in expenses:
            formatted_exp = dict(exp)
            # Formata a data para YYYY-MM-DD
            formatted_exp['expense_date'] = formatted_exp['expense_date'].strftime('%Y-%m-%d')
            formatted_expenses.append(formatted_exp)
        return jsonify(formatted_expenses)
    return jsonify({"error": "Erro ao buscar despesas"}), 500

# --- 4. ROTAS DE AUTENTICAÇÃO E SESSÃO ---

@app.route('/admin/login', methods=['POST'])
def admin_login():
    """Autenticação de admin."""
    data = request.json
    password = data.get('password')
    if password == ADMIN_KEY:
        session['is_admin'] = True
        return jsonify({"message": "Login de admin bem-sucedido"}), 200
    return jsonify({"error": "Senha incorreta"}), 401

@app.route('/logout', methods=['POST'])
def logout():
    """Logout de admin."""
    session.pop('is_admin', None)
    return jsonify({"message": "Logout realizado"}), 200

@app.route('/check_auth', methods=['GET'])
def check_auth():
    """Checa o status de autenticação."""
    return jsonify({"is_admin": session.get('is_admin', False)})

# --- 5. ROTA PRINCIPAL (INDEX) ---

@app.route('/')
def index():
    """Renderiza a página HTML principal."""
    # Definição inicial da estrutura do banco de dados (CREATE TABLE IF NOT EXISTS)
    # Isto garante que o DB esteja pronto na primeira execução.
    setup_db_schema()

    html_content = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BarberFlow - Agendamento e Gestão</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap');
        body {{ font-family: 'Inter', sans-serif; background-color: #1a1a2e; color: #ffffff; }}
        .header-gradient {{ background: linear-gradient(90deg, #ff7e5f 0%, #feb47b 100%); }}
        .btn-primary {{ background-color: #ff7e5f; color: #1a1a2e; font-weight: 600; transition: all 0.3s; }}
        .btn-primary:hover {{ background-color: #feb47b; }}
        .btn-secondary {{ background-color: #3b3b54; color: #ffffff; transition: all 0.3s; }}
        .btn-secondary:hover {{ background-color: #4f4f6b; }}
        .card {{ background-color: #2a2a4a; border-radius: 0.75rem; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -2px rgba(0, 0, 0, 0.1); }}
        .input-style {{ background-color: #3b3b54; border: 1px solid #4f4f6b; color: #ffffff; padding: 0.75rem; border-radius: 0.5rem; width: 100%; }}
        .tab-active {{ border-bottom: 3px solid #ff7e5f; color: #ff7e5f; }}
        .loading-overlay {{ position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0, 0, 0, 0.7); display: flex; justify-content: center; align-items: center; z-index: 50; }}
        .spinner {{ border: 4px solid rgba(255, 255, 255, 0.1); border-top-color: #ff7e5f; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; }}
        @keyframes spin {{ 0% {{ transform: rotate(0deg); }} 100% {{ transform: rotate(360deg); }} }}
        /* Estilos para a rolagem horizontal nos tabs (responsividade) */
        .tab-scroll-container {{ overflow-x: auto; white-space: nowrap; -webkit-overflow-scrolling: touch; }}
        .tab-scroll-container::-webkit-scrollbar {{ display: none; }}
        .tab-scroll-container {{ -ms-overflow-style: none; scrollbar-width: none; }}
    </style>
</head>
<body class="min-h-screen">
    <div id="loading-overlay" class="loading-overlay hidden">
        <div class="spinner"></div>
    </div>

    <!-- Modal para mensagens e confirmações -->
    <div id="modal-overlay" class="modal-overlay fixed inset-0 bg-black bg-opacity-75 z-40 hidden flex items-center justify-center p-4" onclick="closeModal()">
        <div id="modal-content" class="card p-6 w-11/12 md:w-full max-w-lg transition-all transform scale-100 duration-300" onclick="event.stopPropagation()">
            <h3 id="modal-title" class="text-xl font-bold mb-4 text-[#ff7e5f]"></h3>
            <p id="modal-message" class="mb-6"></p>
            <div id="modal-actions" class="flex justify-end space-x-3">
                <button onclick="closeModal()" class="btn-secondary px-4 py-2 rounded-lg">Fechar</button>
            </div>
        </div>
    </div>

    <!-- Container Principal (Responsivo) -->
    <div class="max-w-7xl mx-auto p-4 sm:p-6 lg:p-8">
        
        <!-- Cabeçalho -->
        <header class="header-gradient p-4 sm:p-6 rounded-xl mb-6 shadow-xl">
            <div class="flex flex-col sm:flex-row justify-between items-center">
                <h1 class="text-3xl sm:text-4xl font-extrabold text-[#1a1a2e] mb-2 sm:mb-0">BarberFlow</h1>
                <div id="view-switch-container" class="flex flex-col sm:flex-row space-y-2 sm:space-y-0 sm:space-x-4">
                    <button id="agendamento-btn" onclick="changeView('booking')" class="btn-secondary px-4 py-2 rounded-lg text-sm sm:text-base">Agendamento</button>
                    <button id="admin-btn" onclick="handleRoleSelection()" class="btn-secondary px-4 py-2 rounded-lg text-sm sm:text-base">Admin</button>
                    <button id="logout-btn" onclick="logout()" class="btn-secondary px-4 py-2 rounded-lg text-sm sm:text-base hidden">Logout</button>
                </div>
            </div>
        </header>

        <!-- Área de visualização principal -->
        <div id="app-view-container">
            
            <!-- Login View -->
            <div id="login-view" class="hidden">
                <div class="card p-6 max-w-sm mx-auto">
                    <h2 class="text-2xl font-bold mb-4 text-[#ff7e5f]">Acesso Administrativo</h2>
                    <input type="password" id="admin-password" placeholder="Senha de Admin" class="input-style mb-4">
                    <button onclick="handleAdminLogin()" class="btn-primary w-full py-2 rounded-lg">Entrar</button>
                    <p id="login-error" class="text-red-400 mt-3 text-center hidden">Senha incorreta</p>
                </div>
            </div>

            <!-- Booking View (Agendamento) -->
            <div id="booking-view" class="hidden card p-4 sm:p-6">
                <h2 class="text-2xl font-bold mb-4 text-[#ff7e5f]">Agende seu Horário</h2>
                <form id="appointment-form" class="space-y-4">
                    <!-- Informações do Cliente -->
                    <div>
                        <label for="client_name" class="block text-sm font-medium mb-1">Seu Nome:</label>
                        <input type="text" id="client_name" name="client_name" required class="input-style">
                    </div>
                    <div>
                        <label for="client_phone" class="block text-sm font-medium mb-1">Telefone (Whatsapp):</label>
                        <input type="tel" id="client_phone" name="client_phone" required class="input-style">
                    </div>
                    
                    <!-- Seleção de Serviço -->
                    <div>
                        <label for="service_id" class="block text-sm font-medium mb-1">Serviço:</label>
                        <select id="service_id" name="service_id" required class="input-style">
                            <option value="">Selecione um serviço</option>
                        </select>
                        <p id="service-info" class="text-sm mt-1 text-gray-400"></p>
                    </div>

                    <!-- Seleção de Data -->
                    <div>
                        <label for="appointment_date" class="block text-sm font-medium mb-2">Selecione a Data:</label>
                        <div id="date-picker-container" class="grid grid-cols-3 md:grid-cols-7 gap-2">
                            <!-- Botões de data serão injetados aqui -->
                        </div>
                        <input type="hidden" id="appointment_date" name="date" required>
                    </div>

                    <!-- Seleção de Horário -->
                    <div id="time-slot-area" class="hidden">
                        <label for="appointment_time" class="block text-sm font-medium mb-2">Horários Disponíveis:</label>
                        <div id="time-slots-container" class="flex flex-wrap gap-2 max-h-60 overflow-y-auto card p-3">
                            <!-- Slots de horário serão injetados aqui -->
                        </div>
                        <input type="hidden" id="appointment_time" name="time" required>
                    </div>
                    
                    <button type="submit" class="btn-primary w-full py-3 rounded-lg text-lg">Confirmar Agendamento</button>
                    <p id="booking-message" class="mt-4 text-center text-green-400 hidden"></p>
                </form>
            </div>

            <!-- Schedule View (Agenda do Admin) -->
            <div id="schedule-view" class="hidden card p-4 sm:p-6">
                <div class="flex flex-col sm:flex-row sm:justify-between sm:items-center mb-4 space-y-3 sm:space-y-0">
                    <h2 class="text-2xl font-bold text-[#ff7e5f]">Agenda de Hoje</h2>
                    <input type="date" id="schedule_date_picker" class="input-style max-w-full sm:max-w-xs">
                </div>

                <div id="schedule-list" class="space-y-4">
                    <!-- Agendamentos serão injetados aqui -->
                </div>
                <p id="schedule-empty-message" class="text-center text-gray-400 mt-6 hidden">Nenhum agendamento para a data selecionada.</p>
            </div>

            <!-- Admin View -->
            <div id="admin-view" class="hidden">
                <h2 class="text-3xl font-bold mb-4 text-[#ff7e5f] text-center sm:text-left">Painel Administrativo</h2>
                
                <!-- Navegação por Tabs (Responsiva) -->
                <div class="tab-scroll-container border-b border-[#3b3b54] mb-6">
                    <div class="flex space-x-4">
                        <button class="tab-btn px-4 py-2 font-medium tab-active" data-tab="dashboard" onclick="changeAdminTab('dashboard')">Dashboard</button>
                        <button class="tab-btn px-4 py-2 font-medium" data-tab="services" onclick="changeAdminTab('services')">Serviços</button>
                        <button class="tab-btn px-4 py-2 font-medium" data-tab="expenses" onclick="changeAdminTab('expenses')">Despesas</button>
                        <button class="tab-btn px-4 py-2 font-medium" data-tab="archive" onclick="changeAdminTab('archive')">Arquivados</button>
                    </div>
                </div>

                <!-- Conteúdo dos Tabs -->
                <div id="admin-content-container">
                    
                    <!-- Dashboard Tab -->
                    <div id="dashboard-tab" class="admin-tab-content">
                        <h3 class="text-xl font-bold mb-4">Resumo Financeiro</h3>
                        
                        <div class="flex flex-col sm:flex-row sm:space-x-3 mb-6 space-y-3 sm:space-y-0">
                            <input type="month" id="summary-month-year" class="input-style sm:max-w-xs" value="{current_month_year_str}">
                            <button onclick="loadMonthlySummary()" class="btn-secondary px-4 py-2 rounded-lg">Ver Resumo</button>
                        </div>

                        <!-- Cards de Estatísticas (Responsivos) -->
                        <div id="dashboard-stats" class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
                            <div class="card p-4 text-center">
                                <p class="text-2xl font-bold text-green-400" id="stat-revenue">R$ 0,00</p>
                                <p class="text-sm text-gray-400">Receita (Concluído)</p>
                            </div>
                            <div class="card p-4 text-center">
                                <p class="text-2xl font-bold text-red-400" id="stat-expenses">R$ 0,00</p>
                                <p class="text-sm text-gray-400">Total Despesas</p>
                            </div>
                            <div class="card p-4 text-center">
                                <p class="text-2xl font-bold text-red-300" id="stat-fixed-expense">R$ 0,00</p>
                                <p class="text-sm text-gray-400">Desp. Fixa</p>
                            </div>
                            <div class="card p-4 text-center">
                                <p class="text-2xl font-bold" id="stat-profit">R$ 0,00</p>
                                <p class="text-sm text-gray-400">Lucro Líquido</p>
                            </div>
                        </div>

                        <h3 class="text-xl font-bold mb-4">Gerenciamento Rápido</h3>
                        <!-- Link para a agenda de hoje -->
                        <button onclick="changeView('schedule')" class="btn-primary w-full sm:w-auto py-2 px-4 rounded-lg">Ver Agenda do Dia</button>
                    </div>

                    <!-- Services Tab -->
                    <div id="services-tab" class="admin-tab-content hidden">
                        <h3 class="text-xl font-bold mb-4">Cadastro de Serviços</h3>
                        <form id="service-form" class="card p-4 sm:p-6 mb-6 grid grid-cols-1 sm:grid-cols-4 gap-4 items-end">
                            <input type="hidden" id="service_id_edit">
                            <div class="sm:col-span-2">
                                <label for="service_name" class="block text-sm font-medium mb-1">Nome:</label>
                                <input type="text" id="service_name" required class="input-style">
                            </div>
                            <div>
                                <label for="service_price" class="block text-sm font-medium mb-1">Preço (R$):</label>
                                <input type="number" step="0.01" id="service_price" required class="input-style">
                            </div>
                            <div>
                                <label for="service_duration" class="block text-sm font-medium mb-1">Duração (min):</label>
                                <input type="number" step="15" id="service_duration" required class="input-style">
                            </div>
                            <div class="sm:col-span-4 flex space-x-3">
                                <button type="submit" class="btn-primary flex-grow py-2 rounded-lg" id="service-submit-btn">Adicionar Serviço</button>
                                <button type="button" onclick="clearServiceForm()" class="btn-secondary py-2 px-4 rounded-lg" id="service-cancel-btn" style="display: none;">Cancelar Edição</button>
                            </div>
                        </form>

                        <h3 class="text-xl font-bold mb-4 mt-8">Lista de Serviços</h3>
                        <div id="services-list" class="space-y-3">
                            <!-- Lista de serviços será injetada aqui -->
                        </div>
                    </div>

                    <!-- Expenses Tab -->
                    <div id="expenses-tab" class="admin-tab-content hidden">
                        <h3 class="text-xl font-bold mb-4">Lançamento de Despesa Variável</h3>
                        <form id="expense-form" class="card p-4 sm:p-6 mb-6 grid grid-cols-1 sm:grid-cols-4 gap-4 items-end">
                            <div class="sm:col-span-2">
                                <label for="expense_description" class="block text-sm font-medium mb-1">Descrição:</label>
                                <input type="text" id="expense_description" required class="input-style">
                            </div>
                            <div>
                                <label for="expense_amount" class="block text-sm font-medium mb-1">Valor (R$):</label>
                                <input type="number" step="0.01" id="expense_amount" required class="input-style">
                            </div>
                            <div>
                                <label for="expense_date" class="block text-sm font-medium mb-1">Data:</label>
                                <input type="date" id="expense_date" required class="input-style" value="{today_date_str}">
                            </div>
                            <div class="sm:col-span-4">
                                <button type="submit" class="btn-primary w-full py-2 rounded-lg">Adicionar Despesa</button>
                            </div>
                        </form>

                        <h3 class="text-xl font-bold mb-4 mt-8">Despesas Variáveis Lançadas (<span id="expense-month-display">{current_month_name}</span>)</h3>
                        <div class="flex mb-4">
                             <input type="month" id="expense-month-year" class="input-style max-w-full sm:max-w-xs" value="{current_month_year_str}" onchange="loadExpenses()">
                        </div>
                        
                        <div id="expenses-list" class="space-y-3">
                            <!-- Lista de despesas será injetada aqui -->
                        </div>
                        <p id="expenses-empty-message" class="text-center text-gray-400 mt-6 hidden">Nenhuma despesa variável lançada para este mês.</p>
                    </div>
                    
                    <!-- Archive Tab -->
                    <div id="archive-tab" class="admin-tab-content hidden">
                        <h3 class="text-xl font-bold mb-4">Agendamentos Arquivados</h3>
                        <p class="text-gray-400 mb-4">Estes são agendamentos que foram finalizados e movidos para o arquivo.</p>
                        
                        <div class="flex flex-col sm:flex-row sm:space-x-3 mb-6 space-y-3 sm:space-y-0">
                            <input type="date" id="archive_date_picker" class="input-style max-w-full sm:max-w-xs">
                            <button onclick="loadArchivedAppointments()" class="btn-secondary px-4 py-2 rounded-lg">Buscar Data</button>
                        </div>

                        <div id="archived-list" class="space-y-4">
                            <!-- Agendamentos arquivados serão injetados aqui -->
                        </div>
                        <p id="archive-empty-message" class="text-center text-gray-400 mt-6 hidden">Nenhum agendamento arquivado para a data selecionada.</p>
                    </div>

                </div>
            </div>
        </div>
    </div>

    <script>
        // --- VARIÁVEIS GLOBAIS ---
        let currentView = 'booking';
        let currentAdminTab = 'dashboard';
        let servicesData = [];

        // Tradução dos nomes dos meses para o display (necessário para a função loadExpenses/Summary)
        const monthNames = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
            "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"
        ];
        
        // --- UI E UTILS ---

        function showModal(title, message, actions = null) {{
            document.getElementById('modal-title').textContent = title;
            document.getElementById('modal-message').textContent = message;
            
            const actionContainer = document.getElementById('modal-actions');
            actionContainer.innerHTML = '';
            
            // Botão padrão de fechar
            const closeButton = document.createElement('button');
            closeButton.textContent = 'Fechar';
            closeButton.onclick = closeModal;
            closeButton.className = 'btn-secondary px-4 py-2 rounded-lg';
            actionContainer.appendChild(closeButton);

            if (actions) {{
                // Adiciona botões de ação customizados ANTES do botão Fechar
                actions.forEach(action => {{
                    const btn = document.createElement('button');
                    btn.textContent = action.text;
                    btn.onclick = () => {{
                        closeModal();
                        action.callback();
                    }};
                    btn.className = action.is_primary ? 'btn-primary px-4 py-2 rounded-lg' : 'btn-secondary px-4 py-2 rounded-lg';
                    actionContainer.insertBefore(btn, closeButton);
                }});
            }}

            document.getElementById('modal-overlay').classList.remove('hidden');
        }}

        function closeModal() {{
            document.getElementById('modal-overlay').classList.add('hidden');
        }}

        function showLoading(isVisible) {{
            const overlay = document.getElementById('loading-overlay');
            if (isVisible) {{
                overlay.classList.remove('hidden');
            }} else {{
                overlay.classList.add('hidden');
            }}
        }}

        function changeView(newView) {{
            if (newView === 'schedule' && !session.is_admin) {{
                newView = 'booking'; // Fallback se não for admin
            }}

            currentView = newView;
            document.querySelectorAll('#app-view-container > div').forEach(el => {{
                el.classList.add('hidden');
            }});
            
            // Mostra a view correta
            const viewElement = document.getElementById(newView + '-view');
            if (viewElement) {{
                viewElement.classList.remove('hidden');
            }} else if (newView === 'schedule') {{
                // Se for 'schedule', garante que a view de admin (que contém schedule) está visível
                document.getElementById('admin-view').classList.remove('hidden');
            }}
            
            // Gerencia os botões de navegação
            const adminBtn = document.getElementById('admin-btn');
            const logoutBtn = document.getElementById('logout-btn');
            const agendamentoBtn = document.getElementById('agendamento-btn');
            const viewSwitchContainer = document.getElementById('view-switch-container');

            if (session.is_admin) {{
                adminBtn.classList.add('hidden');
                logoutBtn.classList.remove('hidden');
                viewSwitchContainer.classList.remove('hidden');
            }} else {{
                adminBtn.classList.remove('hidden');
                logoutBtn.classList.add('hidden');
                viewSwitchContainer.classList.remove('hidden');
            }}

            if (newView === 'schedule' || newView === 'admin') {{
                // Garante que o painel de admin esteja visível
                document.getElementById('admin-view').classList.remove('hidden');
                changeAdminTab(newAdminTab || 'dashboard'); // Redireciona para o dashboard ou tab atual
                document.getElementById('schedule-date-picker').value = new Date().toISOString().split('T')[0];
                loadAppointments();
            }} else if (newView === 'booking') {{
                loadServices().then(renderDateButtons);
            }}

            // Atualiza o texto do botão Admin/Agendamento
            if (session.is_admin) {{
                 if (newView === 'schedule') {{
                    agendamentoBtn.textContent = 'Agendamento (Cliente)';
                 }} else {{
                    agendamentoBtn.textContent = 'Agenda (Admin)';
                 }}
            }}
        }}

        function changeAdminTab(newTab) {{
            currentAdminTab = newTab;
            
            document.querySelectorAll('.admin-tab-content').forEach(el => el.classList.add('hidden'));
            document.getElementById(newTab + '-tab').classList.remove('hidden');
            
            document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('tab-active'));
            document.querySelector(`.tab-btn[data-tab="${newTab}"]`).classList.add('tab-active');

            if (newTab === 'services') {{
                loadServices(true); // Recarrega para o admin
            }} else if (newTab === 'expenses') {{
                loadExpenses();
            }} else if (newTab === 'dashboard') {{
                loadMonthlySummary();
            }} else if (newTab === 'archive') {{
                // Garante que a data de arquivados é a de hoje ao abrir
                document.getElementById('archive_date_picker').value = new Date().toISOString().split('T')[0];
                loadArchivedAppointments();
            }}
        }}
        
        function formatCurrency(amount) {{
            return new Intl.NumberFormat('pt-BR', {{ style: 'currency', currency: 'BRL' }}).format(amount);
        }}

        // --- AUTENTICAÇÃO E SESSÃO ---

        const session = {{ is_admin: false, init: false }};

        function handleRoleSelection() {{
            if (session.is_admin) {{
                // Se já for admin, vai direto para a agenda
                changeView('schedule');
            }} else {{
                // Se não for admin, mostra o login
                document.getElementById('login-view').classList.remove('hidden');
                document.getElementById('booking-view').classList.add('hidden');
            }}
        }}

        function handleAdminLogin() {{
            showLoading(true);
            const password = document.getElementById('admin-password').value;
            fetch('/admin/login', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ password: password }})
            }})
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.message) {{
                    session.is_admin = true;
                    document.getElementById('login-view').classList.add('hidden');
                    document.getElementById('admin-password').value = '';
                    changeView('schedule'); // Vai para a agenda ao logar
                }} else {{
                    document.getElementById('login-error').classList.remove('hidden');
                    setTimeout(() => document.getElementById('login-error').classList.add('hidden'), 3000);
                }}
            }})
            .catch(error => {{
                showLoading(false);
                console.error('Login error:', error);
                showModal('Erro', 'Não foi possível conectar ao servidor de login.');
            }});
        }}

        function logout() {{
            showLoading(true);
            fetch('/logout', {{ method: 'POST' }})
            .then(() => {{
                session.is_admin = false;
                document.getElementById('logout-btn').classList.add('hidden');
                document.getElementById('admin-btn').classList.remove('hidden');
                document.getElementById('login-view').classList.add('hidden');
                showLoading(false);
                changeView('booking'); // Volta para a view de agendamento cliente
            }})
            .catch(() => {{
                showLoading(false);
                showModal('Erro', 'Não foi possível fazer logout.');
            }});
        }}

        // --- FUNÇÕES DE CARREGAMENTO DE DADOS ---
        
        function loadServices(isAdmin = false) {{
            return new Promise((resolve, reject) => {{
                fetch('/api/services')
                .then(res => res.json())
                .then(data => {{
                    servicesData = data;
                    if (!isAdmin) {{
                        renderServiceSelect(data);
                    }} else {{
                        renderServicesList(data);
                    }}
                    resolve(data);
                }})
                .catch(error => {{
                    console.error('Erro ao carregar serviços:', error);
                    showModal('Erro', 'Não foi possível carregar a lista de serviços.');
                    reject(error);
                }});
            }});
        }}

        function loadAppointments() {{
            const date = document.getElementById('schedule_date_picker').value;
            if (!date) return;
            showLoading(true);
            
            fetch(`/api/appointments/${{date}}`)
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.error) {{
                    showModal('Erro', data.error);
                    return;
                }}
                renderAppointmentsList(data);
            }})
            .catch(error => {{
                showLoading(false);
                console.error('Erro ao carregar agendamentos:', error);
                showModal('Erro', 'Não foi possível carregar os agendamentos.');
            }});
        }}
        
        function loadArchivedAppointments() {{
            const date = document.getElementById('archive_date_picker').value;
            if (!date) return;
            showLoading(true);
            
            fetch(`/api/appointments/${{date}}?include_archived=true`)
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.error) {{
                    showModal('Erro', data.error);
                    return;
                }}
                // Filtra apenas os arquivados para esta view
                const archived = data.filter(app => app.archived === true);
                renderArchivedList(archived);
            }})
            .catch(error => {{
                showLoading(false);
                console.error('Erro ao carregar arquivados:', error);
                showModal('Erro', 'Não foi possível carregar os agendamentos arquivados.');
            }});
        }}

        function loadAppointmentSlots(date) {{
            showLoading(true);
            return fetch(`/api/slots/${{date}}`)
                .then(res => res.json())
                .then(data => {{
                    showLoading(false);
                    if (data.error) {{
                        showModal('Erro', data.error);
                        return [];
                    }}
                    return data;
                }})
                .catch(error => {{
                    showLoading(false);
                    console.error('Erro ao carregar slots:', error);
                    showModal('Erro', 'Não foi possível carregar os horários disponíveis.');
                    return [];
                }});
        }}

        function loadMonthlySummary() {{
            const monthYearInput = document.getElementById('summary-month-year').value;
            if (!monthYearInput) return;
            
            const [year, month] = monthYearInput.split('-').map(Number);
            if (!month || !year) return;

            showLoading(true);
            fetch(`/api/admin/summary/${{month}}/${{year}}`)
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.error) {{
                    showModal('Erro', data.error);
                    return;
                }}
                document.getElementById('stat-revenue').textContent = formatCurrency(data.total_revenue);
                document.getElementById('stat-expenses').textContent = formatCurrency(data.total_expense);
                document.getElementById('stat-fixed-expense').textContent = formatCurrency(data.fixed_expense);
                
                const profitElement = document.getElementById('stat-profit');
                profitElement.textContent = formatCurrency(data.net_profit);
                
                // Cor do lucro
                if (data.net_profit >= 0) {{
                    profitElement.classList.remove('text-red-400');
                    profitElement.classList.add('text-green-400');
                }} else {{
                    profitElement.classList.remove('text-green-400');
                    profitElement.classList.add('text-red-400');
                }}
            }})
            .catch(error => {{
                showLoading(false);
                console.error('Erro ao carregar resumo:', error);
                showModal('Erro', 'Não foi possível carregar o resumo financeiro.');
            }});
        }}

        function loadExpenses() {{
            const monthYearInput = document.getElementById('expense-month-year').value;
            if (!monthYearInput) return;
            
            const [year, month] = monthYearInput.split('-').map(Number);
            if (!month || !year) return;

            // Atualiza o display do mês
            document.getElementById('expense-month-display').textContent = monthNames[month - 1];

            showLoading(true);
            fetch(`/api/admin/expenses/${{month}}/${{year}}`)
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.error) {{
                    showModal('Erro', data.error);
                    return;
                }}
                renderExpensesList(data);
            }})
            .catch(error => {{
                showLoading(false);
                console.error('Erro ao carregar despesas:', error);
                showModal('Erro', 'Não foi possível carregar as despesas.');
            }});
        }}

        // --- FUNÇÕES DE RENDERIZAÇÃO ---
        
        function renderServiceSelect(services) {{
            const select = document.getElementById('service_id');
            select.innerHTML = '<option value="">Selecione um serviço</option>';
            services.forEach(service => {{
                const option = document.createElement('option');
                option.value = service.id;
                option.textContent = `\${{service.name}} (\${{formatCurrency(service.price)}} - \${{service.duration_minutes}} min)`;
                option.dataset.price = service.price;
                option.dataset.duration = service.duration_minutes;
                select.appendChild(option);
            }});
            
            // Adiciona listener para mostrar info do serviço
            select.onchange = () => {{
                const selectedOption = select.options[select.selectedIndex];
                const infoElement = document.getElementById('service-info');
                if (selectedOption.value) {{
                    infoElement.textContent = `Preço: \${{formatCurrency(parseFloat(selectedOption.dataset.price))}} | Duração: \${{selectedOption.dataset.duration}} minutos.`;
                }} else {{
                    infoElement.textContent = '';
                }}
                
                // Reseta a seleção de horário ao mudar o serviço
                document.getElementById('appointment_date').value = '';
                document.getElementById('appointment_time').value = '';
                document.getElementById('time-slot-area').classList.add('hidden');
                document.querySelectorAll('.day-btn.selected').forEach(btn => btn.classList.remove('selected', 'bg-red-500'));
            }};
        }}

        function renderServicesList(services) {{
            const listContainer = document.getElementById('services-list');
            listContainer.innerHTML = '';

            if (services.length === 0) {{
                listContainer.innerHTML = '<p class="text-center text-gray-400">Nenhum serviço cadastrado.</p>';
                return;
            }}

            services.forEach(service => {{
                const div = document.createElement('div');
                div.className = 'card p-3 flex flex-col sm:flex-row justify-between items-start sm:items-center space-y-2 sm:space-y-0';
                div.innerHTML = `
                    <div>
                        <p class="font-semibold text-lg">\${{service.name}}</p>
                        <p class="text-sm text-gray-400">\${{formatCurrency(service.price)}} / \${{service.duration_minutes}} min</p>
                    </div>
                    <div class="flex space-x-2">
                        <button onclick="editService(\${{service.id}})" class="btn-secondary px-3 py-1 rounded-lg text-sm">Editar</button>
                        <button onclick="deleteService(\${{service.id}})" class="bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded-lg text-sm">Excluir</button>
                    </div>
                `;
                listContainer.appendChild(div);
            }});
        }}

        function renderDateButtons() {{
            const container = document.getElementById('date-picker-container');
            container.innerHTML = '';
            
            // Cria botões para os próximos 7 dias
            for (let i = 0; i < 7; i++) {{
                const day = new Date();
                day.setDate(day.getDate() + i);
                const dayStr = day.toISOString().split('T')[0];
                const dayName = ['Dom', 'Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb'][day.getDay()];
                
                const button = document.createElement('button');
                button.className = 'day-btn btn-secondary px-3 py-2 rounded-lg text-center transition-colors duration-200';
                button.dataset.date = dayStr;
                button.innerHTML = `
                    <span class="block font-bold text-sm">\${{dayName}}</span>
                    <span class="block text-xs">\${{day.getDate()}}/{{day.getMonth() + 1}}</span>
                `;
                button.onclick = () => selectAppointmentDate(dayStr, button);
                container.appendChild(button);
            }}
        }}

        function renderTimeSlots(slots) {{
            const container = document.getElementById('time-slots-container');
            container.innerHTML = '';
            const timeInput = document.getElementById('appointment_time');
            timeInput.value = ''; // Reseta a seleção de horário

            if (slots.length === 0) {{
                container.innerHTML = '<p class="text-center text-gray-400">Nenhum horário disponível.</p>';
                document.getElementById('time-slot-area').classList.remove('hidden');
                return;
            }}

            slots.forEach(slot => {{
                const button = document.createElement('button');
                button.className = 'slot-btn btn-secondary px-3 py-1 rounded-lg text-sm transition-colors duration-200';
                button.textContent = slot;
                button.dataset.time = slot;
                button.onclick = () => selectAppointmentTime(slot, button);
                container.appendChild(button);
            }});
            
            document.getElementById('time-slot-area').classList.remove('hidden');
        }}

        function renderAppointmentsList(appointments) {{
            const listContainer = document.getElementById('schedule-list');
            listContainer.innerHTML = '';
            
            if (appointments.length === 0) {{
                document.getElementById('schedule-empty-message').classList.remove('hidden');
                return;
            }}
            document.getElementById('schedule-empty-message').classList.add('hidden');

            appointments.forEach(app => {{
                let statusColor = '';
                let statusText = '';
                switch (app.status) {{
                    case 'Agendado': statusColor = 'bg-yellow-500'; statusText = 'Agendado'; break;
                    case 'Confirmado': statusColor = 'bg-blue-500'; statusText = 'Confirmado'; break;
                    case 'Cancelado': statusColor = 'bg-red-500'; statusText = 'Cancelado'; break;
                    case 'Concluído': statusColor = 'bg-green-500'; statusText = 'Concluído'; break;
                    default: statusColor = 'bg-gray-500'; statusText = 'Desconhecido';
                }}
                
                const appCard = document.createElement('div');
                appCard.className = `card p-4 flex flex-col sm:flex-row justify-between items-start sm:items-center space-y-3 sm:space-y-0 border-l-4 rounded-lg \${{app.archived ? 'border-gray-500 opacity-60' : 'border-[#ff7e5f]'}}`;
                appCard.innerHTML = `
                    <!-- Detalhes (Ocupa 3/4 no desktop, full na mobile) -->
                    <div class="w-full sm:w-3/4 space-y-1">
                        <p class="text-xl font-bold text-white">\${{app.appointment_time}} - \${{app.service_name}}</p>
                        <p class="text-gray-300">Cliente: \${{app.client_name}}</p>
                        <p class="text-gray-400">Tel: \${{app.client_phone}}</p>
                        <div class="mt-2">
                            <span class="inline-block px-3 py-1 text-xs font-semibold rounded-full text-black \${{statusColor}}">\${{statusText}}</span>
                            \${{app.archived ? '<span class="inline-block px-3 py-1 text-xs font-semibold rounded-full bg-gray-600 text-white ml-2">Arquivado</span>' : ''}}
                        </div>
                    </div>
                    
                    <!-- Ações (Ocupa 1/4 no desktop, full na mobile) -->
                    <div class="w-full sm:w-1/4 flex flex-wrap gap-2 justify-end">
                        <select onchange="updateAppointmentStatus(\${{app.id}}, this.value)" class="input-style text-sm py-1 px-2 rounded-lg \${{app.archived ? 'hidden' : ''}}">
                            <option value="Agendado" \${{app.status === 'Agendado' ? 'selected' : ''}}>Agendado</option>
                            <option value="Confirmado" \${{app.status === 'Confirmado' ? 'selected' : ''}}>Confirmado</option>
                            <option value="Concluído" \${{app.status === 'Concluído' ? 'selected' : ''}}>Concluído</option>
                            <option value="Cancelado" \${{app.status === 'Cancelado' ? 'selected' : ''}}>Cancelado</option>
                        </select>
                        <button onclick="archiveAppointment(\${{app.id}})" class="btn-secondary px-3 py-1 rounded-lg text-sm \${{app.archived ? 'hidden' : ''}}">Arquivar</button>
                    </div>
                `;
                listContainer.appendChild(appCard);
            }});
        }}
        
        function renderArchivedList(appointments) {{
            const listContainer = document.getElementById('archived-list');
            listContainer.innerHTML = '';

            if (appointments.length === 0) {{
                document.getElementById('archive-empty-message').classList.remove('hidden');
                return;
            }}
            document.getElementById('archive-empty-message').classList.add('hidden');

            appointments.forEach(app => {{
                let statusColor = '';
                switch (app.status) {{
                    case 'Concluído': statusColor = 'bg-green-500'; break;
                    case 'Cancelado': statusColor = 'bg-red-500'; break;
                    default: statusColor = 'bg-gray-500';
                }}
                
                const appCard = document.createElement('div');
                appCard.className = `card p-4 flex justify-between items-center border-l-4 border-gray-500 opacity-80`;
                appCard.innerHTML = `
                    <div>
                        <p class="font-bold">\${{app.appointment_time}} - \${{app.service_name}}</p>
                        <p class="text-sm text-gray-400">\${{app.client_name}} | Status: <span class="text-white \${{statusColor}} px-2 py-0.5 rounded text-xs">\${{app.status}}</span></p>
                    </div>
                `;
                listContainer.appendChild(appCard);
            }});
        }}


        function renderExpensesList(expenses) {{
            const listContainer = document.getElementById('expenses-list');
            listContainer.innerHTML = '';

            if (expenses.length === 0) {{
                document.getElementById('expenses-empty-message').classList.remove('hidden');
                return;
            }}
            document.getElementById('expenses-empty-message').classList.add('hidden');

            expenses.forEach(expense => {{
                const div = document.createElement('div');
                div.className = 'card p-3 flex flex-col sm:flex-row justify-between items-start sm:items-center space-y-2 sm:space-y-0 border-l-4 border-red-500';
                div.innerHTML = `
                    <div class="w-full sm:w-2/3">
                        <p class="font-semibold text-lg">\${{expense.description}}</p>
                        <p class="text-sm text-gray-400">Data: \${{expense.expense_date}}</p>
                    </div>
                    <div class="flex items-center space-x-3 w-full sm:w-1/3 justify-between sm:justify-end">
                        <p class="text-red-400 font-bold">\${{formatCurrency(expense.amount)}}</p>
                        <button onclick="deleteExpense(\${{expense.id}})" class="bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded-lg text-sm">Excluir</button>
                    </div>
                `;
                listContainer.appendChild(div);
            }});
        }}


        // --- FUNÇÕES DE INTERAÇÃO DO CLIENTE ---

        function selectAppointmentDate(dateStr, button) {{
            const serviceId = document.getElementById('service_id').value;
            if (!serviceId) {{
                showModal('Atenção', 'Por favor, selecione um serviço primeiro.');
                return;
            }}

            document.querySelectorAll('.day-btn').forEach(btn => btn.classList.remove('bg-red-500', 'btn-primary', 'selected'));
            button.classList.add('bg-red-500', 'selected'); // Usa cor de destaque
            document.getElementById('appointment_date').value = dateStr;
            document.getElementById('appointment_time').value = '';
            
            // Carregar slots para a data
            loadAppointmentSlots(dateStr).then(slots => {{
                renderTimeSlots(slots);
            }});
        }}

        function selectAppointmentTime(timeStr, button) {{
            document.querySelectorAll('.slot-btn').forEach(btn => btn.classList.remove('btn-primary', 'selected'));
            button.classList.add('btn-primary', 'selected');
            document.getElementById('appointment_time').value = timeStr;
        }}

        function handleAppointmentSubmit(event) {{
            event.preventDefault();
            showLoading(true);

            const form = event.target;
            const client_name = form.client_name.value;
            const client_phone = form.client_phone.value;
            const service_id = form.service_id.value;
            const date = form.date.value;
            const time = form.time.value;

            if (!date || !time) {{
                showLoading(false);
                showModal('Atenção', 'Por favor, selecione a data e o horário.');
                return;
            }}

            const payload = {{ client_name, client_phone, service_id: parseInt(service_id), date, time }};
            
            fetch('/api/appointments', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }})
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                const msgElement = document.getElementById('booking-message');
                msgElement.classList.remove('hidden');
                
                if (data.message) {{
                    msgElement.textContent = data.message;
                    msgElement.classList.remove('text-red-400');
                    msgElement.classList.add('text-green-400');
                    form.reset();
                    // Limpar e renderizar novamente datas/slots
                    document.getElementById('appointment_date').value = '';
                    document.getElementById('appointment_time').value = '';
                    document.getElementById('time-slot-area').classList.add('hidden');
                    loadServices().then(renderDateButtons); 
                }} else {{
                    msgElement.textContent = data.error || 'Erro desconhecido ao agendar.';
                    msgElement.classList.remove('text-green-400');
                    msgElement.classList.add('text-red-400');
                }}
            }})
            .catch(error => {{
                showLoading(false);
                showModal('Erro', 'Não foi possível completar o agendamento devido a um erro de rede.');
            }});
        }}

        // --- FUNÇÕES DE INTERAÇÃO DO ADMIN ---

        function updateAppointmentStatus(appId, status) {{
            showLoading(true);
            fetch(`/api/appointments/\${{appId}}/status`, {{
                method: 'PUT',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ status: status }})
            }})
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.message) {{
                    loadAppointments(); // Recarrega a lista
                }} else {{
                    showModal('Erro', data.error);
                }}
            }})
            .catch(() => {{
                showLoading(false);
                showModal('Erro', 'Não foi possível atualizar o status.');
            }});
        }}
        
        function archiveAppointment(appId) {{
            showModal(
                'Confirmar Arquivamento',
                'Tem certeza que deseja arquivar este agendamento?',
                [{{
                    text: 'Arquivar',
                    is_primary: true,
                    callback: () => {{
                        showLoading(true);
                        fetch(`/api/appointments/\${{appId}}/archive`, {{ method: 'PUT' }})
                        .then(res => res.json())
                        .then(data => {{
                            showLoading(false);
                            if (data.message) {{
                                loadAppointments(); // Recarrega a lista da agenda atual
                                showModal('Sucesso', data.message);
                            }} else {{
                                showModal('Erro', data.error);
                            }}
                        }})
                        .catch(() => {{
                            showLoading(false);
                            showModal('Erro', 'Não foi possível arquivar o agendamento.');
                        }});
                    }}
                }}]
            );
        }}

        // Gerenciamento de Serviços
        function clearServiceForm() {{
            document.getElementById('service-form').reset();
            document.getElementById('service_id_edit').value = '';
            document.getElementById('service-submit-btn').textContent = 'Adicionar Serviço';
            document.getElementById('service-cancel-btn').style.display = 'none';
        }}

        function editService(id) {{
            const service = servicesData.find(s => s.id === id);
            if (!service) return;
            
            document.getElementById('service_id_edit').value = service.id;
            document.getElementById('service_name').value = service.name;
            document.getElementById('service_price').value = service.price;
            document.getElementById('service_duration').value = service.duration_minutes;
            document.getElementById('service-submit-btn').textContent = 'Atualizar Serviço';
            document.getElementById('service-cancel-btn').style.display = 'inline-block';
        }}
        
        function deleteService(id) {{
            showModal(
                'Confirmar Exclusão',
                'Tem certeza que deseja excluir este serviço? Esta ação não pode ser desfeita.',
                [{{
                    text: 'Excluir',
                    is_primary: true,
                    callback: () => {{
                        showLoading(true);
                        fetch(`/api/admin/services/\${{id}}`, {{ method: 'DELETE' }})
                        .then(res => res.json())
                        .then(data => {{
                            showLoading(false);
                            if (data.message) {{
                                showModal('Sucesso', data.message);
                                loadServices(true); // Recarrega a lista de serviços admin
                            }} else {{
                                showModal('Erro', data.error);
                            }}
                        }})
                        .catch(() => {{
                            showLoading(false);
                            showModal('Erro', 'Não foi possível excluir o serviço.');
                        }});
                    }}
                }}]
            );
        }}

        function handleServiceSubmit(event) {{
            event.preventDefault();
            showLoading(true);

            const id = document.getElementById('service_id_edit').value;
            const name = document.getElementById('service_name').value;
            const price = parseFloat(document.getElementById('service_price').value);
            const duration_minutes = parseInt(document.getElementById('service_duration').value);

            const method = id ? 'PUT' : 'POST';
            const payload = id ? {{ id: parseInt(id), name, price, duration_minutes }} : {{ name, price, duration_minutes }};

            fetch('/api/admin/services', {{
                method: method,
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }})
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.message) {{
                    showModal('Sucesso', data.message);
                    clearServiceForm();
                    loadServices(true); // Recarrega a lista de serviços admin
                }} else {{
                    showModal('Erro', data.error);
                }}
            }})
            .catch(() => {{
                showLoading(false);
                showModal('Erro', 'Não foi possível salvar o serviço.');
            }});
        }}

        // Gerenciamento de Despesas
        function handleExpenseSubmit(event) {{
            event.preventDefault();
            showLoading(true);

            const form = event.target;
            const description = form.expense_description.value;
            const amount = parseFloat(form.expense_amount.value);
            const expense_date = form.expense_date.value;
            
            if (isNaN(amount) || amount <= 0) {{
                showLoading(false);
                showModal('Atenção', 'Valor da despesa inválido.');
                return;
            }}

            const payload = {{ description, amount, expense_date }};

            fetch('/api/admin/expenses', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }})
            .then(res => res.json())
            .then(data => {{
                showLoading(false);
                if (data.message) {{
                    showModal('Sucesso', data.message);
                    form.reset();
                    // Garante que o input de data volte para a data atual, se aplicável
                    form.expense_date.value = new Date().toISOString().split('T')[0]; 
                    loadExpenses(); // Recarrega a lista de despesas
                    loadMonthlySummary(); // Atualiza o dashboard
                }} else {{
                    showModal('Erro', data.error);
                }}
            }})
            .catch(() => {{
                showLoading(false);
                showModal('Erro', 'Não foi possível adicionar a despesa.');
            }});
        }}
        
        function deleteExpense(id) {{
            showModal(
                'Confirmar Exclusão',
                'Tem certeza que deseja excluir esta despesa?',
                [{{
                    text: 'Excluir',
                    is_primary: true,
                    callback: () => {{
                        showLoading(true);
                        fetch(`/api/admin/expenses/\${{id}}`, {{ method: 'DELETE' }})
                        .then(res => res.json())
                        .then(data => {{
                            showLoading(false);
                            if (data.message) {{
                                showModal('Sucesso', data.message);
                                loadExpenses(); // Recarrega a lista de despesas
                                loadMonthlySummary(); // Atualiza o dashboard
                            }} else {{
                                showModal('Erro', data.error);
                            }}
                        }})
                        .catch(() => {{
                            showLoading(false);
                            showModal('Erro', 'Não foi possível excluir a despesa.');
                        }});
                    }}
                }}]
            );
        }}


        // --- INICIALIZAÇÃO E EVENT LISTENERS ---

        document.addEventListener('DOMContentLoaded', () => {{
            // Inicialização da data do seletor da agenda e do arquivo
            const today = new Date().toISOString().split('T')[0];
            document.getElementById('schedule_date_picker').value = today;
            document.getElementById('schedule_date_picker').addEventListener('change', loadAppointments);
            
            document.getElementById('archive_date_picker').value = today;
            // O listener para archive_date_picker é no botão, para não carregar a cada mudança

            // Event listener para o formulário de agendamento
            document.getElementById('appointment-form').addEventListener('submit', handleAppointmentSubmit);

            // Event listener para o formulário de serviço
            document.getElementById('service-form').addEventListener('submit', handleServiceSubmit);
            
            // Event listener para o formulário de despesa
            document.getElementById('expense-form').addEventListener('submit', handleExpenseSubmit);
            
            // Inicialização da view
            initApp();
        }});

        function initApp() {{
            showLoading(true);
            fetch('/check_auth')
                .then(res => res.json())
                .then(data => {{
                    session.is_admin = data.is_admin;
                    session.init = true;

                    if (session.is_admin) {{
                        document.getElementById('login-view').classList.add('hidden');
                        document.getElementById('booking-view').classList.add('hidden');
                        document.getElementById('admin-view').classList.remove('hidden');
                        document.getElementById('logout-btn').classList.remove('hidden');
                        document.getElementById('admin-btn').classList.add('hidden');
                        
                        // Garante que o container de botões de visualização aparece para admin
                        document.getElementById('view-switch-container').classList.remove('hidden');
                        changeView('schedule');
                    }} else {{
                        document.getElementById('login-view').classList.add('hidden');
                        document.getElementById('booking-view').classList.remove('hidden');
                        document.getElementById('admin-view').classList.add('hidden');
                        document.getElementById('logout-btn').classList.add('hidden');
                        document.getElementById('admin-btn').classList.remove('hidden');
                        
                        // Garante que o container de botões de visualização aparece
                        document.getElementById('view-switch-container').classList.remove('hidden');
                        changeView('booking');
                    }}
                    showLoading(false); 
                }})
                .catch(() => {{
                    // Em caso de erro, assume-se não logado e mostra a view de agendamento.
                    session.is_admin = false;
                    document.getElementById('login-view').classList.add('hidden');
                    document.getElementById('booking-view').classList.remove('hidden');
                    document.getElementById('admin-view').classList.add('hidden');
                    showLoading(false);
                    document.getElementById('view-switch-container').classList.remove('hidden');
                    changeView('booking');
                }});
        }}


        // --- EXPOSIÇÃO GLOBAL DE FUNÇÕES ---
        window.changeView = changeView;
        window.handleAppointmentSubmit = handleAppointmentSubmit;
        window.changeAdminTab = changeAdminTab;
        window.closeModal = closeModal;
        window.updateAppointmentStatus = updateAppointmentStatus;
        window.archiveAppointment = archiveAppointment; // NOVO
        window.loadArchivedAppointments = loadArchivedAppointments; // NOVO
        window.handleServiceSubmit = handleServiceSubmit;
        window.clearServiceForm = clearServiceForm;
        window.editService = editService;
        window.deleteService = deleteService;
        window.handleExpenseSubmit = handleExpenseSubmit;
        window.deleteExpense = deleteExpense;
        window.loadMonthlySummary = loadMonthlySummary;
        window.loadExpenses = loadExpenses;
        window.loadAppointments = loadAppointments;
        window.handleRoleSelection = handleRoleSelection;
        window.handleAdminLogin = handleAdminLogin;
        window.logout = logout;

    </script>
</body>
</html>
"""
    
    # Prepara dados de data para inputs
    today = date.today()
    current_month_year_str = today.strftime('%Y-%m')
    today_date_str = today.strftime('%Y-%m-%d')
    current_month_name = calendar.month_name[today.month]

    # Renderiza o template com as variáveis de data
    return render_template_string(html_content, 
                                  current_month_year_str=current_month_year_str,
                                  today_date_str=today_date_str,
                                  current_month_name=current_month_name)

def setup_db_schema():
    """Cria as tabelas do DB se não existirem."""
    conn = get_db_connection()
    if conn is None:
        return

    try:
        with conn.cursor() as cur:
            # Tabela de Serviços
            cur.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(255) NOT NULL,
                    price DECIMAL(10, 2) NOT NULL,
                    duration_minutes INTEGER NOT NULL
                );
            """)

            # Tabela de Agendamentos
            cur.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    id SERIAL PRIMARY KEY,
                    client_name VARCHAR(255) NOT NULL,
                    client_phone VARCHAR(20),
                    service_id INTEGER REFERENCES services(id) ON DELETE RESTRICT,
                    appointment_datetime TIMESTAMP WITH TIME ZONE NOT NULL,
                    status VARCHAR(50) NOT NULL DEFAULT 'Agendado',
                    archived BOOLEAN DEFAULT FALSE
                );
            """)
            
            # Tabela de Despesas
            cur.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id SERIAL PRIMARY KEY,
                    description VARCHAR(255) NOT NULL,
                    amount DECIMAL(10, 2) NOT NULL,
                    expense_date DATE NOT NULL
                );
            """)
            
            conn.commit()
    except Exception as e:
        print(f"Erro ao configurar o esquema do banco de dados: {e}")
        conn.rollback()
    finally:
        conn.close()


if __name__ == '__main__':
    # Configurações de execução
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
