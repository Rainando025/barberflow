# -*- coding: utf-8 -*-
import os
import psycopg2
import psycopg2.extras 
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from datetime import datetime, date

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
FIXED_EXPENSES = 1500.00

def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados."""
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        print(f"Erro ao conectar ao PostgreSQL: {e}")
        return None

def initialize_db():
    """Cria as tabelas Services, Appointments, Monthly_Expenses e garante a coluna 'is_archived'."""
    conn = get_db_connection()
    if conn is None:
        return

    try:
        with conn.cursor() as cur:
            # Tabela de Serviços
            cur.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    price NUMERIC(10, 2) NOT NULL,
                    duration INTEGER NOT NULL
                );
            """)

            # Tabela de Agendamentos (Adicionando a coluna is_archived no SQL base para garantir)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS appointments (
                    id SERIAL PRIMARY KEY,
                    barber_id VARCHAR(50) NOT NULL,
                    service_name VARCHAR(100) NOT NULL,
                    appointment_date DATE NOT NULL,
                    appointment_time VARCHAR(10) NOT NULL,
                    client_name VARCHAR(100) NOT NULL,
                    client_phone VARCHAR(20),
                    client_email VARCHAR(100),
                    service_price NUMERIC(10, 2) NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'Agendado',
                    is_archived BOOLEAN NOT NULL DEFAULT FALSE, -- NOVA COLUNA
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Tenta adicionar a coluna is_archived se não existir
            try:
                cur.execute("ALTER TABLE appointments ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT FALSE;")
                print("Coluna 'is_archived' adicionada à tabela appointments (se não existisse).")
            except psycopg2.errors.DuplicateColumn:
                pass # Coluna já existe, ignora o erro

            # Tabela de Despesas Mensais
            cur.execute("""
                CREATE TABLE IF NOT EXISTS monthly_expenses (
                    id SERIAL PRIMARY KEY,
                    description VARCHAR(255) NOT NULL,
                    amount NUMERIC(10, 2) NOT NULL,
                    expense_date DATE NOT NULL DEFAULT CURRENT_DATE
                );
            """)

            # Adicionar serviços mock se a tabela estiver vazia
            cur.execute("SELECT COUNT(*) FROM services;")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO services (name, price, duration) VALUES ('Corte Simples', 35.00, 45);")
                cur.execute("INSERT INTO services (name, price, duration) VALUES ('Design de Barba', 25.00, 30);")
                cur.execute("INSERT INTO services (name, price, duration) VALUES ('Corte + Barba', 55.00, 75);")
                print("Serviços mock inseridos.")

            # Adicionar despesas mock se a tabela estiver vazia
            cur.execute("SELECT COUNT(*) FROM monthly_expenses;")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO monthly_expenses (description, amount) VALUES ('Aluguel (Mock)', 1200.00);")
                cur.execute("INSERT INTO monthly_expenses (description, amount) VALUES ('Energia (Mock)', 200.00);")
                print("Despesas mock inseridas.")

        conn.commit()
    except Exception as e:
        print(f"Erro ao inicializar o banco de dados: {e}")
        conn.rollback()
    finally:
        conn.close()

# Inicializa o banco de dados assim que o script é executado
initialize_db()

# --- 2. APLICAÇÃO FLASK E ROTAS API ---

app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

def get_role():
    """Obtém a função do usuário da sessão Flask."""
    return session.get('role', 'none')

# --- Rotas de Autenticação e Configuração (Mantidas) ---

@app.route('/', methods=['GET'])
def index():
    """Serve o arquivo HTML com o frontend."""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/login', methods=['POST'])
def login():
    """Simula o login de cliente/admin."""
    data = request.get_json()
    role = data.get('role')
    admin_key = data.get('adminKey')

    if role == 'admin' and admin_key == ADMIN_KEY:
        session['role'] = 'admin'
        return jsonify({'message': 'Login Admin bem-sucedido', 'role': 'admin'}), 200
    elif role == 'client':
        session['role'] = 'client'
        return jsonify({'message': 'Login Cliente bem-sucedido', 'role': 'client'}), 200
    
    session['role'] = 'none'
    return jsonify({'message': 'Chave de acesso incorreta ou função inválida'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    """Faz o logout do usuário."""
    session.pop('role', None)
    return jsonify({'message': 'Logout bem-sucedido'}), 200

# --- Rotas de Serviços (Mantidas) ---

@app.route('/api/services', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_services():
    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    role = get_role()
    
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if request.method == 'GET':
                cur.execute("SELECT id, name, price, duration FROM services ORDER BY name;")
                services = cur.fetchall()
                return jsonify(services)

            if role != 'admin':
                return jsonify({'message': 'Acesso negado. Apenas Barbeiros (Admin) podem gerenciar serviços.'}), 403

            if request.method == 'POST':
                # POST é usado para criação (sem 'id') ou edição (com 'id')
                data = request.get_json()
                service_id = data.get('id')
                name = data.get('name')
                price = data.get('price')
                duration = data.get('duration')
                
                if service_id:
                    # Edição (PUT lógico)
                    cur.execute("UPDATE services SET name = %s, price = %s, duration = %s WHERE id = %s RETURNING id;",
                                (name, price, duration, service_id))
                    conn.commit()
                    return jsonify({'message': f'Serviço ID {service_id} atualizado com sucesso.'})
                else:
                    # Criação (POST)
                    cur.execute("INSERT INTO services (name, price, duration) VALUES (%s, %s, %s) RETURNING id;",
                                (name, price, duration))
                    new_id = cur.fetchone()['id']
                    conn.commit()
                    return jsonify({'message': f'Serviço "{name}" adicionado com ID {new_id}.'}), 201

            elif request.method == 'DELETE':
                data = request.get_json()
                service_id = data.get('id')
                cur.execute("DELETE FROM services WHERE id = %s RETURNING id;", (service_id,))
                if cur.fetchone():
                    conn.commit()
                    return jsonify({'message': f'Serviço ID {service_id} excluído com sucesso.'})
                return jsonify({'message': 'Serviço não encontrado.'}), 404

    except Exception as e:
        conn.rollback()
        print(f"Erro na gestão de serviços: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()

# --- Rotas de Agendamentos (Atualizadas) ---

@app.route('/api/appointments', methods=['GET', 'POST'])
def manage_appointments():
    """GET: Lista agendamentos ATIVOS. POST: Cria novo agendamento."""
    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    role = get_role()

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if request.method == 'GET':
                if role != 'admin':
                    return jsonify({'message': 'Acesso negado. Apenas Barbeiros (Admin) podem ver agendamentos.'}), 403
                
                # RETORNA APENAS AGENDAMENTOS NÃO ARQUIVADOS (is_archived = FALSE)
                cur.execute("SELECT id, barber_id, service_name, appointment_date, appointment_time, client_name, service_price, status FROM appointments WHERE is_archived = FALSE ORDER BY appointment_date, appointment_time;")
                appointments = cur.fetchall()
                for appt in appointments:
                    appt['appointment_date'] = appt['appointment_date'].strftime('%Y-%m-%d')
                return jsonify(appointments)

            elif request.method == 'POST':
                data = request.get_json()
                barber_id = data.get('barberId')
                service_name = data.get('serviceName')
                appointment_date = data.get('date')
                appointment_time = data.get('time')
                client_name = data.get('clientName')
                client_phone = data.get('clientPhone')
                client_email = data.get('clientEmail')
                service_price = data.get('servicePrice')
                
                cur.execute("""
                    INSERT INTO appointments (barber_id, service_name, appointment_date, appointment_time, client_name, client_phone, client_email, service_price)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
                """, (barber_id, service_name, appointment_date, appointment_time, client_name, client_phone, client_email, service_price))
                
                new_id = cur.fetchone()['id']
                conn.commit()
                return jsonify({'message': f'Agendamento submetido com ID {new_id}.', 'id': new_id}), 201

    except Exception as e:
        conn.rollback()
        print(f"Erro na gestão de agendamentos: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/appointments/<int:id>/status', methods=['PUT'])
def update_appointment_status(id):
    """Atualiza o status (Concluído/Cancelado/Agendado) de um agendamento."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado. Apenas Barbeiros (Admin) podem atualizar status.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        data = request.get_json()
        new_status = data.get('status')

        with conn.cursor() as cur:
            cur.execute("UPDATE appointments SET status = %s WHERE id = %s RETURNING id;", (new_status, id))
            if cur.fetchone():
                conn.commit()
                return jsonify({'message': f'Status do Agendamento {id} atualizado para {new_status}.'})
            return jsonify({'message': 'Agendamento não encontrado.'}), 404
            
    except Exception as e:
        conn.rollback()
        print(f"Erro ao atualizar status: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()

# --- NOVA ROTA: LISTA DE AGENDAMENTOS ARQUIVADOS ---

@app.route('/api/appointments/archived', methods=['GET'])
def get_archived_appointments():
    """Retorna a lista de agendamentos ARQUIVADOS (is_archived = TRUE)."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Apenas agendamentos ARQUIVADOS
            cur.execute("SELECT id, barber_id, service_name, appointment_date, appointment_time, client_name, service_price, status FROM appointments WHERE is_archived = TRUE ORDER BY appointment_date DESC, appointment_time DESC;")
            appointments = cur.fetchall()
            for appt in appointments:
                appt['appointment_date'] = appt['appointment_date'].strftime('%Y-%m-%d')
            return jsonify(appointments)

    except Exception as e:
        print(f"Erro ao obter agendamentos arquivados: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()


# --- NOVA ROTA: ARQUIVAR/DESARQUIVAR AGENDAMENTO ---

@app.route('/api/appointments/<int:id>/archive', methods=['PUT'])
def archive_appointment(id):
    """Arquiva ou desarquiva (toggle) um agendamento."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        data = request.get_json()
        # O padrão é arquivar (True) se não for especificado
        is_archived = data.get('isArchived', True) 

        with conn.cursor() as cur:
            cur.execute("UPDATE appointments SET is_archived = %s WHERE id = %s RETURNING id;", (is_archived, id))
            if cur.fetchone():
                conn.commit()
                action = 'Arquivado' if is_archived else 'Desarquivado'
                return jsonify({'message': f'Agendamento {id} {action} com sucesso.'})
            return jsonify({'message': 'Agendamento não encontrado.'}), 404
            
    except Exception as e:
        conn.rollback()
        print(f"Erro ao arquivar/desarquivar agendamento: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()

# --- Rotas de Despesas (Mantidas) ---

@app.route('/api/expenses', methods=['GET', 'POST'])
def manage_expenses():
    """Gerencia a listagem e adição de despesas mensais."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado. Apenas Barbeiros (Admin) podem gerenciar despesas.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if request.method == 'GET':
                # Pega despesas do mês atual
                cur.execute("""
                    SELECT id, description, amount, expense_date 
                    FROM monthly_expenses
                    WHERE EXTRACT(MONTH FROM expense_date) = EXTRACT(MONTH FROM CURRENT_DATE)
                    AND EXTRACT(YEAR FROM expense_date) = EXTRACT(YEAR FROM CURRENT_DATE)
                    ORDER BY expense_date DESC;
                """)
                expenses = cur.fetchall()
                for exp in expenses:
                    exp['expense_date'] = exp['expense_date'].strftime('%Y-%m-%d')
                    exp['amount'] = float(exp['amount'])
                return jsonify(expenses)

            elif request.method == 'POST':
                data = request.get_json()
                description = data.get('description')
                amount = data.get('amount')
                expense_date = data.get('date', datetime.now().strftime('%Y-%m-%d'))
                
                cur.execute("""
                    INSERT INTO monthly_expenses (description, amount, expense_date)
                    VALUES (%s, %s, %s) RETURNING id;
                """, (description, amount, expense_date))
                
                new_id = cur.fetchone()['id']
                conn.commit()
                return jsonify({'message': f'Despesa "{description}" adicionada com ID {new_id}.'}), 201

    except Exception as e:
        conn.rollback()
        print(f"Erro na gestão de despesas: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()

@app.route('/api/expenses/<int:id>', methods=['DELETE'])
def delete_expense(id):
    """Exclui uma despesa pelo ID."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM monthly_expenses WHERE id = %s RETURNING id;", (id,))
            if cur.fetchone():
                conn.commit()
                return jsonify({'message': f'Despesa ID {id} excluída com sucesso.'})
            return jsonify({'message': 'Despesa não encontrada.'}), 404
            
    except Exception as e:
        conn.rollback()
        print(f"Erro ao excluir despesa: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()


# --- ROTA DO DASHBOARD (Mantida) ---

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    """Calcula e retorna dados do dashboard, incluindo dados diários para o gráfico."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Data Inicial do Mês
            first_day_of_month = date.today().replace(day=1).strftime('%Y-%m-%d')
            
            # 1. Receita Total e Agendamentos Concluídos do Mês (Apenas Agendamentos NÃO ARQUIVADOS)
            cur.execute("""
                SELECT SUM(service_price) as total_revenue, COUNT(*) as completed_count
                FROM appointments
                WHERE status = 'Concluído' 
                AND is_archived = FALSE -- APENAS NÃO ARQUIVADOS
                AND appointment_date >= %s;
            """, (first_day_of_month,))
            
            revenue_result = cur.fetchone()
            total_revenue = float(revenue_result['total_revenue'] or 0.0)
            completed_count = revenue_result['completed_count'] or 0

            # 2. Despesas Totais do Mês
            cur.execute("""
                SELECT SUM(amount) as total_expenses
                FROM monthly_expenses
                WHERE expense_date >= %s;
            """, (first_day_of_month,))
            
            expense_result = cur.fetchone()
            total_expenses = float(expense_result['total_expenses'] or 0.0)

            net_income = total_revenue - total_expenses
            
            # 3. Dados Diários para o Gráfico (últimos 30 dias de agendamentos CONCLUÍDOS e NÃO ARQUIVADOS)
            cur.execute("""
                SELECT 
                    appointment_date,
                    SUM(service_price) as daily_revenue,
                    COUNT(*) as daily_appointments
                FROM appointments
                WHERE status = 'Concluído' 
                AND is_archived = FALSE -- APENAS NÃO ARQUIVADOS
                AND appointment_date >= (CURRENT_DATE - INTERVAL '30 days')
                GROUP BY appointment_date
                ORDER BY appointment_date;
            """)
            daily_data_raw = cur.fetchall()
            
            daily_data = []
            for row in daily_data_raw:
                date_str = row['appointment_date'].strftime('%Y-%m-%d')
                daily_data.append({
                    'date': date_str,
                    'revenue': float(row['daily_revenue'] or 0.0),
                    'appointments': row['daily_appointments'] or 0
                })

            return jsonify({
                'totalRevenue': total_revenue,
                'completedAppointments': completed_count,
                'totalExpenses': total_expenses,
                'netIncome': net_income,
                'dailyData': daily_data 
            })

    except Exception as e:
        print(f"Erro ao obter dados do dashboard: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()


# --- 3. CONTEÚDO HTML E JAVASCRIPT (ATUALIZADO) ---

# O frontend é injetado como um template de string em Flask.
HTML_TEMPLATE = f"""
<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BarberFlow - Agendamento e Gestão (Python/Postgres)</title>
    <!-- Carrega Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- Carrega Chart.js para gráficos dinâmicos -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
    
    <!-- Configuração de Fonte Inter -->
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap');
        body {{
            font-family: 'Inter', sans-serif;
            background-color: #f3f4f6;
        }}
        #loading-overlay {{
            z-index: 50;
        }}
        .chart-container {{
            position: relative;
            height: 400px;
            width: 100%;
        }}
    </style>
</head>
<body class="min-h-screen antialiased">

    <!-- Navbar simples (visível após o login/seleção de função) -->
    <header id="app-header" class="bg-gray-900 shadow-md hidden">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-4 flex justify-between items-center">
            <h1 class="text-3xl font-extrabold text-white tracking-tight">Barber<span class="text-red-500">Flow</span></h1>
            
            <!-- Seletor de Modo (Switch de Agendamento para Admin) -->
            <div id="view-switch-container" class="flex items-center space-x-4">
                <button id="switch-schedule" onclick="changeView('schedule')" class="px-4 py-2 text-sm font-medium rounded-full transition-colors duration-200" style="background-color: #dc2626; color: white;">
                    Agendamento Cliente
                </button>
                <button id="switch-admin" onclick="changeView('admin')" class="px-4 py-2 text-sm font-medium rounded-full text-white bg-gray-700 hover:bg-gray-800 transition-colors duration-200">
                    Área Barbeiro
                </button>
            </div>
            <button onclick="logout()" class="px-3 py-1 text-sm font-medium rounded-full text-red-400 border border-red-400 hover:bg-red-400 hover:text-white transition-colors duration-200">Sair</button>
        </div>
    </header>

    <!-- Container Principal do App -->
    <main class="py-10">
        <div id="app-container" class="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8">
            
            <!-- 0. TELA DE LOGIN -->
            <div id="login-view" class="flex justify-center items-center h-[60vh]">
                <div class="bg-white p-8 rounded-xl shadow-2xl border-t-4 border-red-600 w-full max-w-md">
                    <h2 class="text-3xl font-bold text-gray-900 mb-6 text-center">Acesso BarberFlow</h2>
                    <p class="text-sm text-gray-500 mb-6 text-center">Escolha sua forma de acesso para continuar.</p>
                    
                    <button onclick="handleRoleSelection('client')" class="w-full mb-4 py-3 px-4 rounded-lg shadow-md text-base font-medium text-white bg-green-600 hover:bg-green-700 transition-all duration-200 transform hover:scale-[1.01]">
                        Agendar um Serviço (Cliente)
                    </button>
                    
                    <div class="relative flex py-5 items-center">
                        <div class="flex-grow border-t border-gray-300"></div>
                        <span class="flex-shrink mx-4 text-gray-400 text-sm">OU</span>
                        <div class="flex-grow border-t border-gray-300"></div>
                    </div>

                    <form onsubmit="handleAdminLogin(event)" class="space-y-4">
                        <input type="password" id="admin-key" placeholder="Chave de Acesso do Barbeiro" required class="w-full py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                        <button type="submit" class="w-full py-3 px-4 rounded-lg shadow-md text-base font-medium text-white bg-gray-800 hover:bg-gray-900 transition-all duration-200 transform hover:scale-[1.01]">
                            Entrar como Barbeiro (Admin)
                        </button>
                        <p id="login-message" class="text-center text-sm text-red-500 font-semibold mt-2"></p>
                    </form>
                </div>
            </div>

            <!-- 1. VISTA DO CLIENTE (AGENDAMENTO) -->
            <div id="schedule-view" class="hidden bg-white p-6 md:p-10 rounded-xl shadow-2xl border-t-4 border-red-600">
                <h2 class="text-3xl font-bold text-gray-800 mb-6 border-b pb-2">Agendamento Online</h2>
                <form id="appointment-form" onsubmit="handleAppointmentSubmit(event)" class="space-y-6">
                    
                    <!-- Passo 1: Barbeiro e Serviço -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label for="barber" class="block text-sm font-medium text-gray-700">Escolha o Barbeiro</label>
                            <select id="barber" required class="mt-1 block w-full pl-3 pr-10 py-3 text-base border-gray-300 focus:outline-none focus:ring-red-500 focus:border-red-500 sm:text-sm rounded-lg shadow-sm">
                                <option value="">-- Selecione o Profissional --</option>
                                <option value="barber1">João (Especialista em Fade)</option>
                                <option value="barber2">Pedro (Especialista em Clássico)</option>
                            </select>
                        </div>
                        <div>
                            <label for="service" class="block text-sm font-medium text-gray-700">Escolha o Corte/Serviço</label>
                            <select id="service" required class="mt-1 block w-full pl-3 pr-10 py-3 text-base border-gray-300 focus:outline-none focus:ring-red-500 focus:border-red-500 sm:text-sm rounded-lg shadow-sm">
                                <option value="">-- Carregando Serviços... --</option>
                            </select>
                        </div>
                    </div>

                    <!-- Passo 2: Data e Hora -->
                    <div>
                        <h3 class="text-lg font-semibold text-gray-800 mb-3 mt-4">Data e Hora</h3>
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label for="date" class="block text-sm font-medium text-gray-700">Data Desejada</label>
                                <input type="date" id="date" required class="mt-1 block w-full py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            </div>
                            <div>
                                <label for="time" class="block text-sm font-medium text-gray-700">Horários Disponíveis</label>
                                <select id="time" required class="mt-1 block w-full pl-3 pr-10 py-3 text-base border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500 sm:text-sm">
                                    <option value="">-- Selecione o Horário --</option>
                                    <option value="09:00">09:00</option>
                                    <option value="10:30">10:30</option>
                                    <option value="14:00">14:00</option>
                                    <option value="15:30">15:30</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    <!-- Passo 3: Dados do Cliente -->
                    <div>
                        <h3 class="text-lg font-semibold text-gray-800 mb-3 mt-4">Seus Dados</h3>
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                            <input type="text" id="client-name" placeholder="Seu Nome Completo" required class="col-span-3 md:col-span-1 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            <input type="tel" id="client-phone" placeholder="Seu Telefone (Whatsapp)" required class="col-span-3 md:col-span-1 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            <input type="email" id="client-email" placeholder="Seu Email (Opcional)" class="col-span-3 md:col-span-1 py-3 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                        </div>
                    </div>

                    <!-- Botão de Agendar -->
                    <button type="submit" id="submit-button" class="w-full mt-8 py-3 px-4 border border-transparent rounded-lg shadow-md text-base font-medium text-white bg-red-600 hover:bg-red-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 transition-all duration-200 transform hover:scale-[1.01]">
                        Confirmar Agendamento
                    </button>
                </form>
            </div>

            <!-- 2. VISTA DO BARBEIRO (ADMIN) -->
            <div id="admin-view" class="hidden bg-white p-6 md:p-10 rounded-xl shadow-2xl border-t-4 border-red-600">
                <h2 class="text-3xl font-bold text-gray-800 mb-6">Painel do Barbeiro</h2>
                <p id="admin-role-warning" class="bg-red-100 border-l-4 border-red-500 text-red-700 p-3 mb-4 hidden" role="alert">
                    <span class="font-bold">Atenção:</span> Você não possui permissão de Barbeiro (Admin) para acessar esta aba.
                </p>

                <!-- Tabs de Navegação -->
                <div class="border-b border-gray-200">
                    <nav class="-mb-px flex space-x-8" aria-label="Tabs">
                        <button onclick="changeAdminTab('appointments')" id="tab-appointments" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-red-500 text-red-600">
                            Agendamentos Ativos
                        </button>
                        <button onclick="changeAdminTab('archived')" id="tab-archived" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700">
                            Agendamentos Arquivados
                        </button>
                        <button onclick="changeAdminTab('services')" id="tab-services" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700">
                            Serviços e Preços
                        </button>
                        <button onclick="changeAdminTab('expenses')" id="tab-expenses" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700">
                            Despesas
                        </button>
                        <button onclick="changeAdminTab('dashboard')" id="tab-dashboard" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700">
                            Dashboard e Caixa
                        </button>
                    </nav>
                </div>

                <!-- Conteúdo das Tabs -->
                <div class="mt-6 space-y-8">
                    
                    <!-- 2.1. Agendamentos Ativos (NÃO ARQUIVADOS) -->
                    <div id="appointments-tab" class="tab-content">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Próximos Agendamentos (Ativos)</h3>
                        <p class="text-sm text-gray-500 mb-4">Apenas agendamentos Ativos (não arquivados) são exibidos aqui. Arquive os agendamentos antigos/finalizados.</p>
                        <div class="bg-gray-50 p-4 rounded-lg shadow-inner">
                            <p class="text-sm text-gray-600 mb-4">Atualize o status para calcular o Fluxo de Caixa.</p>
                            <div id="appointments-list" class="space-y-4">
                                <p class="text-center text-gray-500">Carregando agendamentos...</p>
                            </div>
                        </div>
                    </div>

                    <!-- 2.1.b. Agendamentos Arquivados (NOVA TAB) -->
                    <div id="archived-tab" class="tab-content hidden">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Agendamentos Arquivados</h3>
                        <p class="text-sm text-gray-500 mb-4">Histórico de agendamentos arquivados. Eles não afetam o dashboard principal, mas podem ser desarquivados.</p>
                        <div class="bg-gray-50 p-4 rounded-lg shadow-inner">
                            <div id="archived-appointments-list" class="space-y-4">
                                <p class="text-center text-gray-500">Carregando agendamentos arquivados...</p>
                            </div>
                        </div>
                    </div>

                    <!-- 2.2. Gerenciamento de Serviços e Preços (Mantida) -->
                    <div id="services-tab" class="tab-content hidden">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Gerenciar Serviços</h3>
                        
                        <!-- Formulário de Edição/Adição de Serviço -->
                        <form id="service-form" onsubmit="handleServiceSubmit(event)" class="bg-gray-50 p-6 rounded-lg shadow mb-6 space-y-4">
                            <h4 class="text-lg font-medium text-gray-700">Adicionar/Editar Serviço</h4>
                            <input type="hidden" id="service-doc-id">
                            <input type="text" id="service-name" placeholder="Nome do Corte/Serviço (Ex: Fade, Barba)" required class="w-full py-2 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            <div class="grid grid-cols-2 gap-4">
                                <input type="number" id="service-price" placeholder="Preço (R$)" required min="0" step="0.01" class="py-2 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                                <input type="number" id="service-duration" placeholder="Duração (minutos)" required min="10" class="py-2 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            </div>
                            <button type="submit" class="w-full py-2 px-4 rounded-lg text-white bg-red-600 hover:bg-red-700 transition-colors duration-200">
                                <span id="service-button-text">Salvar Novo Serviço</span>
                            </button>
                            <button type="button" onclick="clearServiceForm()" id="cancel-service-button" class="w-full py-2 px-4 rounded-lg text-gray-700 bg-gray-200 hover:bg-gray-300 transition-colors duration-200 hidden">
                                Cancelar Edição
                            </button>
                        </form>

                        <!-- Lista de Serviços Atuais -->
                        <ul id="current-services-list" class="space-y-2">
                            <li class="text-center text-gray-500 p-4">Carregando lista de serviços...</li>
                        </ul>
                    </div>

                    <!-- 2.3. Gerenciamento de Despesas (Mantida) -->
                    <div id="expenses-tab" class="tab-content hidden">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Gerenciar Despesas do Mês</h3>
                        
                        <!-- Formulário de Adição de Despesa -->
                        <form id="expense-form" onsubmit="handleExpenseSubmit(event)" class="bg-gray-50 p-6 rounded-lg shadow mb-6 space-y-4">
                            <h4 class="text-lg font-medium text-gray-700">Adicionar Nova Despesa</h4>
                            <input type="text" id="expense-description" placeholder="Descrição (Ex: Aluguel, Compra de Shampo)" required class="w-full py-2 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            <div class="grid grid-cols-2 gap-4">
                                <input type="number" id="expense-amount" placeholder="Valor (R$)" required min="0" step="0.01" class="py-2 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                                <input type="date" id="expense-date" required class="py-2 border border-gray-300 rounded-lg shadow-sm focus:ring-red-500 focus:border-red-500">
                            </div>
                            <button type="submit" class="w-full py-2 px-4 rounded-lg text-white bg-red-600 hover:bg-red-700 transition-colors duration-200">
                                Adicionar Despesa
                            </button>
                        </form>

                        <!-- Lista de Despesas Atuais -->
                        <div class="overflow-x-auto shadow rounded-lg">
                            <table class="min-w-full divide-y divide-gray-200">
                                <thead class="bg-gray-100">
                                    <tr>
                                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Data</th>
                                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Descrição</th>
                                        <th class="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Valor (R$)</th>
                                        <th class="px-6 py-3 text-right text-xs font-medium text-gray-500 uppercase tracking-wider">Ações</th>
                                    </tr>
                                </thead>
                                <tbody id="current-expenses-list" class="bg-white divide-y divide-gray-200">
                                    <tr><td colspan="4" class="text-center text-gray-500 p-4">Carregando despesas...</td></tr>
                                </tbody>
                            </table>
                        </div>
                    </div>


                    <!-- 2.4. Dashboard e Fluxo de Caixa (Mantida) -->
                    <div id="dashboard-tab" class="tab-content hidden">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Dashboard Financeiro (Mês Atual)</h3>
                        
                        <div class="grid grid-cols-1 sm:grid-cols-4 gap-6">
                            
                            <!-- Card 1: Total Recebido -->
                            <div class="bg-green-50 p-6 rounded-xl shadow-lg border-l-4 border-green-600">
                                <p class="text-sm font-medium text-green-700">Receita de Serviços</p>
                                <p id="total-revenue" class="mt-1 text-3xl font-bold text-green-800">R$ 0,00</p>
                                <p class="text-xs text-gray-500 mt-2">Agendamentos CONCLUÍDOS este mês.</p>
                            </div>

                            <!-- Card 2: Agendamentos Concluídos -->
                            <div class="bg-blue-50 p-6 rounded-xl shadow-lg border-l-4 border-blue-600">
                                <p class="text-sm font-medium text-blue-700">Total de Concluídos</p>
                                <p id="total-appointments" class="mt-1 text-3xl font-bold text-blue-800">0</p>
                                <p class="text-xs text-gray-500 mt-2">Agendamentos finalizados (pagos) no mês.</p>
                            </div>

                            <!-- Card 3: Despesas Mensais -->
                            <div class="bg-red-50 p-6 rounded-xl shadow-lg border-l-4 border-red-600">
                                <p class="text-sm font-medium text-red-700">Despesas Totais (Mês)</p>
                                <p id="total-expenses" class="mt-1 text-3xl font-bold text-red-800">R$ 0,00</p>
                                <p class="text-xs text-gray-500 mt-2">Soma das despesas cadastradas.</p>
                            </div>

                            <!-- Card 4: Lucro Líquido (Calculado) -->
                            <div id="net-income-card" class="bg-purple-50 p-6 rounded-xl shadow-lg border-l-4 border-purple-600">
                                <p class="text-sm font-medium text-purple-700">Lucro Líquido</p>
                                <p id="net-income" class="mt-1 text-3xl font-bold text-purple-800">R$ 0,00</p>
                                <p class="text-xs text-gray-500 mt-2">Receita total - Despesas totais (mês).</p>
                            </div>
                        </div>

                        <!-- Gráfico Dinâmico de Fluxo -->
                        <div class="mt-8 bg-white p-6 rounded-xl shadow-md">
                            <h4 class="text-lg font-medium text-gray-700 mb-4">Receita e Agendamentos (Últimos 30 Dias)</h4>
                            <div class="chart-container">
                                <canvas id="revenueChart"></canvas>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

        </div>
    </main>

    <!-- Modal de Mensagem (Substitui alert()) -->
    <div id="message-modal" class="fixed inset-0 bg-gray-600 bg-opacity-75 hidden flex items-center justify-center p-4 transition-opacity duration-300" aria-modal="true" role="dialog">
        <div class="bg-white rounded-lg shadow-xl p-6 w-full max-w-sm transform transition-all">
            <h3 id="modal-title" class="text-xl font-bold text-gray-900 mb-3">Sucesso!</h3>
            <p id="modal-body" class="text-gray-600 mb-5">Mensagem aqui.</p>
            <div class="flex justify-end">
                <button onclick="closeModal()" class="px-4 py-2 text-sm font-medium rounded-lg text-white bg-red-600 hover:bg-red-700 transition-colors duration-200">
                    Fechar
                </button>
            </div>
        </div>
    </div>
    
    <!-- Loading Overlay -->
    <div id="loading-overlay" class="fixed inset-0 bg-gray-900 bg-opacity-50 flex items-center justify-center hidden">
        <div class="flex items-center space-x-2 text-white">
            <svg class="animate-spin h-5 w-5 text-red-500" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
            <span>Processando...</span>
        </div>
    </div>


    <script>
        // Variável global para rastrear a função do usuário
        let userRole = 'none'; 
        const ADMIN_KEY = '{ADMIN_KEY}'; // Injetado do Python
        let revenueChartInstance = null; // Instância global do Chart.js

        // --- Funções de UI e Navegação ---
        let currentView = 'schedule';
        let currentAdminTab = 'appointments';

        function showLoading(show) {{
            document.getElementById('loading-overlay').classList.toggle('hidden', !show);
        }}

        function openModal(title, message, isSuccess = true) {{
            const modal = document.getElementById('message-modal');
            document.getElementById('modal-title').textContent = title;
            document.getElementById('modal-body').textContent = message;
            
            const titleElement = document.getElementById('modal-title');
            if (isSuccess) {{
                titleElement.classList.remove('text-red-700', 'text-green-700');
                titleElement.classList.add('text-green-700');
            }} else {{
                titleElement.classList.remove('text-green-700', 'text-red-700');
                titleElement.classList.add('text-red-700');
            }}
            modal.classList.remove('hidden');
        }}

        function closeModal() {{
            document.getElementById('message-modal').classList.add('hidden');
        }}
        
        // --- Lógica de Login e Permissões (Mantida) ---

        async function handleRoleSelection(role) {{
            showLoading(true);
            const response = await fetch('/api/login', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ role: role }})
            }});
            showLoading(false);

            if (response.ok) {{
                userRole = role;
                document.getElementById('login-view').classList.add('hidden');
                document.getElementById('app-header').classList.remove('hidden');
                
                if (userRole === 'client') {{
                    document.getElementById('view-switch-container').classList.add('hidden');
                    changeView('schedule');
                    loadClientServices();
                }}
            }} else {{
                 openModal('Erro de Login', 'Não foi possível iniciar a sessão como cliente.', false);
            }}
        }}

        async function handleAdminLogin(event) {{
            event.preventDefault();
            const adminKey = document.getElementById('admin-key').value;
            const messageEl = document.getElementById('login-message');
            messageEl.textContent = '';
            
            showLoading(true);
            const response = await fetch('/api/login', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ role: 'admin', adminKey: adminKey }})
            }});
            const data = await response.json();
            showLoading(false);

            if (response.ok) {{
                userRole = 'admin';
                document.getElementById('login-view').classList.add('hidden');
                document.getElementById('app-header').classList.remove('hidden');
                document.getElementById('view-switch-container').classList.remove('hidden');
                changeView('admin');
                loadAdminData();
            }} else {{
                messageEl.textContent = data.message || 'Chave de acesso incorreta.';
                userRole = 'none';
            }}
        }}

        async function logout() {{
            await fetch('/api/logout', {{ method: 'POST' }});
            userRole = 'none';
            // Resetar UI
            document.getElementById('app-header').classList.add('hidden');
            document.getElementById('schedule-view').classList.add('hidden');
            document.getElementById('admin-view').classList.add('hidden');
            document.getElementById('login-view').classList.remove('hidden');
            openModal('Sessão Encerrada', 'Você saiu da sua sessão. Selecione seu perfil para continuar.', true);
        }}

        function changeView(viewName) {{
            if (viewName === 'admin' && userRole !== 'admin') {{
                document.getElementById('admin-role-warning').classList.remove('hidden');
                viewName = currentView === 'admin' ? 'schedule' : currentView;
                if (viewName === 'schedule') {{
                    document.getElementById('schedule-view').classList.remove('hidden');
                    document.getElementById('admin-view').classList.add('hidden');
                }}
                return;
            }}
            
            document.getElementById('admin-role-warning').classList.add('hidden');
            currentView = viewName;

            const scheduleView = document.getElementById('schedule-view');
            const adminView = document.getElementById('admin-view');
            const switchSchedule = document.getElementById('switch-schedule');
            const switchAdmin = document.getElementById('switch-admin');

            if (viewName === 'schedule') {{
                scheduleView.classList.remove('hidden');
                adminView.classList.add('hidden');
                switchSchedule.style.backgroundColor = '#dc2626';
                switchSchedule.style.color = 'white';
                switchAdmin.style.backgroundColor = '#4b5563';
                switchAdmin.style.color = 'white';
                loadClientServices(); 
            }} else {{ 
                scheduleView.classList.add('hidden');
                adminView.classList.remove('hidden');
                switchSchedule.style.backgroundColor = '#4b5563';
                switchSchedule.style.color = 'white';
                switchAdmin.style.backgroundColor = '#dc2626';
                switchAdmin.style.color = 'white';
                loadAdminData(); 
            }}
        }}

        function changeAdminTab(tabName) {{
            currentAdminTab = tabName;

            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            
            document.querySelectorAll('.tab-button').forEach(btn => {{
                btn.classList.remove('border-red-500', 'text-red-600');
                btn.classList.add('border-transparent', 'text-gray-500', 'hover:border-gray-300', 'hover:text-gray-700');
            }});

            document.getElementById(`${{tabName}}-tab`).classList.remove('hidden');
            
            const selectedButton = document.getElementById(`tab-${{tabName}}`);
            selectedButton.classList.remove('border-transparent', 'text-gray-500', 'hover:border-gray-300', 'hover:text-gray-700');
            selectedButton.classList.add('border-red-500', 'text-red-600');
            
            // Carrega dados da aba selecionada
            if (tabName === 'appointments') {{
                loadAppointments();
            }} else if (tabName === 'archived') {{
                loadArchivedAppointments(); // NOVA CHAMADA
            }} else if (tabName === 'services') {{
                loadServicesList();
            }} else if (tabName === 'expenses') {{
                loadExpenses();
            }} else if (tabName === 'dashboard') {{
                loadCashFlow();
            }}
        }}


        // ----------------------------------------------------------------------
        // LÓGICA DO CLIENTE (AGENDAMENTO) - Comunicação com Flask API (Mantida)
        // ----------------------------------------------------------------------

        async function loadClientServices() {{
            const serviceSelect = document.getElementById('service');
            serviceSelect.innerHTML = '<option value="">-- Carregando Serviços... --</option>';

            try {{
                const response = await fetch('/api/services');
                if (!response.ok) throw new Error('Falha ao buscar serviços.');
                const services = await response.json();

                serviceSelect.innerHTML = '<option value="">-- Selecione o Serviço --</option>';
                services.forEach(service => {{
                    const servicePrice = parseFloat(service.price);
                    const formattedPrice = servicePrice.toFixed(2);
                    
                    const option = document.createElement('option');
                    option.value = service.id;
                    option.textContent = `${{service.name}} (R$ ${{formattedPrice.replace('.', ',')}} - ${{service.duration}} min)`;
                    option.setAttribute('data-price', formattedPrice);
                    option.setAttribute('data-name', service.name);
                    serviceSelect.appendChild(option);
                }});
            }} catch (error) {{
                console.error("Erro ao carregar serviços:", error);
                serviceSelect.innerHTML = '<option value="">-- Erro ao carregar serviços --</option>';
            }}
        }}
        
        async function handleAppointmentSubmit(event) {{
            event.preventDefault();
            
            const form = event.target;
            const submitButton = document.getElementById('submit-button');
            const serviceOption = form.service.options[form.service.selectedIndex];
            
            if (!serviceOption || !serviceOption.getAttribute('data-price')) {{
                return openModal('Erro', 'Selecione um serviço válido.', false);
            }}
            
            const servicePrice = parseFloat(serviceOption.getAttribute('data-price'));
            const serviceName = serviceOption.getAttribute('data-name');

            const appointmentData = {{
                barberId: form.barber.value, 
                serviceName: serviceName,
                date: form.date.value,
                time: form.time.value,
                clientName: form['client-name'].value,
                clientPhone: form['client-phone'].value,
                clientEmail: form['client-email'].value,
                servicePrice: servicePrice,
            }};

            showLoading(true);
            submitButton.disabled = true;

            try {{
                const response = await fetch('/api/appointments', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(appointmentData)
                }});

                const result = await response.json();

                if (!response.ok) throw new Error(result.message || 'Erro ao agendar.');
                
                openModal('Agendamento Confirmado!', `Seu agendamento para ${{appointmentData.date}} às ${{appointmentData.time}} foi confirmado. Barbeiro: ${{form.barber.options[form.barber.selectedIndex].text.split('(')[0].trim()}}`, true);
                form.reset();
                
            }} catch (error) {{
                console.error("Erro ao submeter agendamento:", error);
                openModal('Erro no Agendamento', `Não foi possível agendar. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
                submitButton.disabled = false;
            }}
        }}


        // ----------------------------------------------------------------------
        // LÓGICA DO BARBEIRO (ADMIN)
        // ----------------------------------------------------------------------
        
        function loadAdminData() {{
            if (userRole !== 'admin') return; 
            changeAdminTab('appointments');
        }}

        // Função Genérica para Renderizar Agendamentos (Usada para Ativos e Arquivados)
        function renderAppointmentList(appointments, listElementId, isArchivedList = false) {{
            const appointmentsListEl = document.getElementById(listElementId);
            appointmentsListEl.innerHTML = '';

            if (appointments.length === 0) {{
                appointmentsListEl.innerHTML = `<p class="text-center text-gray-500 p-4">Nenhum agendamento ${{isArchivedList ? 'arquivado' : 'ativo'}} encontrado.</p>`;
                return;
            }}

            appointments.forEach((appointment) => {{
                const appointmentId = appointment.id;

                let statusClass = '';
                switch (appointment.status) {{
                    case 'Concluído': statusClass = 'bg-green-100 text-green-800'; break;
                    case 'Cancelado': statusClass = 'bg-red-100 text-red-800'; break;
                    case 'Agendado': default: statusClass = 'bg-yellow-100 text-yellow-800'; break;
                }}

                const barberName = appointment.barber_id === 'barber1' ? 'João' : (appointment.barber_id === 'barber2' ? 'Pedro' : appointment.barber_id);
                const formattedPrice = parseFloat(appointment.service_price).toFixed(2).replace('.', ',');
                
                let actionButton = '';
                if (!isArchivedList) {{
                    // Botão de Arquivar para lista ATIVA
                    actionButton = `<button onclick="archiveAppointment(${{appointmentId}}, true)" class="px-3 py-1 text-xs font-medium rounded-full text-white bg-gray-500 hover:bg-gray-600 transition-colors duration-200">Arquivar</button>`;
                }} else {{
                    // Botão de Desarquivar para lista ARQUIVADA
                    actionButton = `<button onclick="archiveAppointment(${{appointmentId}}, false)" class="px-3 py-1 text-xs font-medium rounded-full text-white bg-blue-500 hover:bg-blue-600 transition-colors duration-200">Desarquivar</button>`;
                }}
                
                const appointmentHtml = `
                    <div id="appt-${{appointmentId}}" class="p-4 bg-white rounded-lg shadow flex flex-col sm:flex-row justify-between items-start sm:items-center transition-all duration-200 hover:shadow-md">
                        <div class="mb-2 sm:mb-0">
                            <p class="font-bold text-lg text-gray-800">${{appointment.appointment_time}} (${{appointment.appointment_date}})</p>
                            <p class="text-sm text-gray-600">${{appointment.client_name}} - ${{appointment.service_name}} c/ ${{barberName}} (R$ ${{formattedPrice}})</p>
                        </div>
                        <div class="flex items-center space-x-2 mt-2 sm:mt-0">
                            <span class="px-3 py-1 text-xs font-semibold rounded-full ${{statusClass}}">${{appointment.status}}</span>
                            ${{isArchivedList ? '' : `
                                <select onchange="updateAppointmentStatus(${{appointmentId}}, this.value)" class="py-1 px-2 border border-gray-300 rounded-lg text-sm focus:ring-red-500 focus:border-red-500">
                                    <option value="Agendado" ${{appointment.status === 'Agendado' ? 'selected' : ''}}>Agendado</option>
                                    <option value="Concluído" ${{appointment.status === 'Concluído' ? 'selected' : ''}}>Concluído</option>
                                    <option value="Cancelado" ${{appointment.status === 'Cancelado' ? 'selected' : ''}}>Cancelado</option>
                                </select>`
                            }}
                            ${{actionButton}}
                        </div>
                    </div>
                `;
                appointmentsListEl.insertAdjacentHTML('beforeend', appointmentHtml);
            }});
        }}

        // Carrega Agendamentos ATIVOS
        async function loadAppointments() {{
            if (userRole !== 'admin') return; 

            const appointmentsListEl = document.getElementById('appointments-list');
            appointmentsListEl.innerHTML = '<p class="text-center text-gray-500">Carregando agendamentos ativos...</p>';
            
            try {{
                const response = await fetch('/api/appointments');
                if (!response.ok) throw new Error('Falha ao buscar agendamentos ativos.');
                const appointments = await response.json();

                renderAppointmentList(appointments, 'appointments-list', false);
                loadCashFlow(); // Recarrega o dashboard
            }} catch (error) {{
                console.error("Erro ao carregar agendamentos ativos:", error);
                openModal('Erro de Dados', error.message, false);
                appointmentsListEl.innerHTML = '<p class="text-center text-red-500 p-4">Erro ao carregar agendamentos ativos.</p>';
            }}
        }}

        // Carrega Agendamentos ARQUIVADOS (NOVA FUNÇÃO)
        async function loadArchivedAppointments() {{
            if (userRole !== 'admin') return; 

            const archivedListEl = document.getElementById('archived-appointments-list');
            archivedListEl.innerHTML = '<p class="text-center text-gray-500">Carregando agendamentos arquivados...</p>';
            
            try {{
                const response = await fetch('/api/appointments/archived');
                if (!response.ok) throw new Error('Falha ao buscar agendamentos arquivados.');
                const appointments = await response.json();

                renderAppointmentList(appointments, 'archived-appointments-list', true);
            }} catch (error) {{
                console.error("Erro ao carregar agendamentos arquivados:", error);
                openModal('Erro de Dados', error.message, false);
                archivedListEl.innerHTML = '<p class="text-center text-red-500 p-4">Erro ao carregar agendamentos arquivados.</p>';
            }}
        }}

        // Arquiva/Desarquiva Agendamento (NOVA FUNÇÃO)
        async function archiveAppointment(id, isArchiving) {{
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem gerenciar o arquivamento.', false);
            showLoading(true);
            try {{
                const response = await fetch(`/api/appointments/${{id}}/archive`, {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ isArchived: isArchiving }})
                }});

                const result = await response.json();
                if (!response.ok) throw new Error(result.message || 'Erro ao arquivar/desarquivar.');

                openModal('Sucesso', result.message, true);
                
                // Recarrega as listas relevantes
                if (isArchiving) {{
                    loadAppointments(); // Recarrega lista de ativos
                }} else {{
                    loadArchivedAppointments(); // Recarrega lista de arquivados
                }}
                loadCashFlow(); // Garante que o dashboard reflita as mudanças
            }} catch (error) {{
                console.error("Erro ao arquivar/desarquivar:", error);
                openModal('Erro', `Não foi possível ${{isArchiving ? 'arquivar' : 'desarquivar'}}. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}
        
        async function updateAppointmentStatus(id, newStatus) {{
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem alterar o status de agendamentos.', false);
            showLoading(true);
            try {{
                const response = await fetch(`/api/appointments/${{id}}/status`, {{
                    method: 'PUT',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ status: newStatus }})
                }});

                const result = await response.json();
                if (!response.ok) throw new Error(result.message || 'Erro ao atualizar status.');

                openModal('Status Atualizado', `Agendamento #${{id}} marcado como "${{newStatus}}".`, true);
                loadAppointments(); 
            }} catch (error) {{
                console.error("Erro ao atualizar status:", error);
                openModal('Erro', `Não foi possível atualizar o status. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}

        // --- Lógica do Dashboard (Mantida) ---
        
        let myChart;
        
        function renderChart(dailyData) {{
            const canvas = document.getElementById('revenueChart');
            
            if (myChart) {{
                myChart.destroy();
            }}
            
            const labels = dailyData.map(d => {{
                const parts = d.date.split('-');
                return `${{parts[2]}}/${{parts[1]}}`; 
            }});
            const revenueData = dailyData.map(d => d.revenue);
            const appointmentsData = dailyData.map(d => d.appointments);

            myChart = new Chart(canvas, {{
                type: 'bar',
                data: {{
                    labels: labels,
                    datasets: [
                        {{
                            label: 'Receita (R$)',
                            data: revenueData,
                            backgroundColor: 'rgba(220, 38, 38, 0.7)',
                            borderColor: 'rgba(220, 38, 38, 1)',
                            yAxisID: 'yRevenue',
                        }},
                        {{
                            label: 'Cortes Concluídos',
                            data: appointmentsData,
                            backgroundColor: 'rgba(59, 130, 246, 0.7)',
                            borderColor: 'rgba(59, 130, 246, 1)',
                            type: 'line', 
                            yAxisID: 'yAppointments',
                            tension: 0.3
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{
                        mode: 'index',
                        intersect: false,
                    }},
                    scales: {{
                        yRevenue: {{
                            type: 'linear',
                            display: true,
                            position: 'left',
                            title: {{ display: true, text: 'Receita (R$)' }},
                            grid: {{ drawOnChartArea: false }},
                            ticks: {{ callback: function(value) {{ return 'R$' + value.toFixed(2).replace('.', ','); }} }}
                        }},
                        yAppointments: {{
                            type: 'linear',
                            display: true,
                            position: 'right',
                            title: {{ display: true, text: 'Cortes Concluídos' }},
                            grid: {{ drawOnChartArea: false }},
                            ticks: {{ precision: 0 }}
                        }}
                    }}
                }}
            }});
        }}

        async function loadCashFlow() {{
            if (userRole !== 'admin') return;
            
            try {{
                const response = await fetch('/api/dashboard');
                if (!response.ok) throw new Error('Falha ao buscar dados do dashboard.');
                const data = await response.json();
                
                const format = (value) => `R$ ${{parseFloat(value).toFixed(2).replace('.', ',')}}`;

                document.getElementById('total-revenue').textContent = format(data.totalRevenue);
                document.getElementById('total-appointments').textContent = data.completedAppointments.toString();
                
                document.getElementById('total-expenses').textContent = format(data.totalExpenses);
                document.getElementById('net-income').textContent = format(data.netIncome);

                const netIncomeCard = document.getElementById('net-income-card');
                const netIncomeText = document.getElementById('net-income');
                
                netIncomeCard.classList.remove('border-purple-600', 'border-red-600');
                netIncomeText.classList.remove('text-purple-800', 'text-red-800');
                
                if (data.netIncome >= 0) {{
                    netIncomeCard.classList.add('border-purple-600');
                    netIncomeText.classList.add('text-purple-800');
                }} else {{
                    netIncomeCard.classList.add('border-red-600');
                    netIncomeText.classList.add('text-red-800');
                }}
                
                renderChart(data.dailyData);
                
            }} catch (error) {{
                 console.error("Erro ao carregar dashboard:", error);
                 // Não abrir modal para evitar sobreposição, apenas loga.
            }}
        }}

        // --- Lógica de Serviços (Mantida) ---

        async function loadServicesList() {{
            if (userRole !== 'admin') return; 
            const servicesListEl = document.getElementById('current-services-list');
            servicesListEl.innerHTML = '<li class="text-center text-gray-500 p-4">Carregando lista de serviços...</li>';

            try {{
                const response = await fetch('/api/services');
                if (!response.ok) throw new Error('Falha ao buscar serviços.');
                const services = await response.json();

                servicesListEl.innerHTML = ''; 
                
                if (services.length === 0) {{
                    servicesListEl.innerHTML = '<li class="text-center text-gray-500 p-4">Nenhum serviço cadastrado.</li>';
                    return;
                }}

                services.forEach((service) => {{
                    const formattedPrice = parseFloat(service.price).toFixed(2).replace('.', ',');
                    
                    const listItem = `
                        <li id="svc-${{service.id}}" class="p-3 bg-white rounded-lg shadow flex justify-between items-center">
                            <div>
                                ${{service.name}} <span class="font-bold text-red-600">R$ ${{formattedPrice}}</span>
                                <span class="text-sm text-gray-500 ml-2">(${{service.duration}} min)</span>
                            </div>
                            <div class="space-x-2">
                                <button onclick="editService(${{service.id}}, '${{service.name.replace(/'/g, "\\'") }}', ${{service.price}}, ${{service.duration}})" class="text-blue-500 hover:text-blue-700 text-sm">Editar</button>
                                <button onclick="deleteService(${{service.id}}, '${{service.name.replace(/'/g, "\\'") }}')" class="text-red-500 hover:text-red-700 text-sm">Excluir</button>
                            </div>
                        </li>
                    `;
                    servicesListEl.insertAdjacentHTML('beforeend', listItem);
                }});
                
            }} catch (error) {{
                console.error("Erro ao carregar serviços:", error);
                openModal('Erro de Dados', error.message, false);
                servicesListEl.innerHTML = '<li class="text-center text-red-500 p-4">Erro ao carregar serviços.</li>';
            }}
        }}
        
        async function handleServiceSubmit(event) {{
            event.preventDefault();
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem gerenciar serviços.', false);
            
            const form = event.target;
            const docId = form['service-doc-id'].value;
            const url = '/api/services';
            
            const serviceData = {{
                id: docId ? parseInt(docId) : null,
                name: form['service-name'].value,
                price: parseFloat(form['service-price'].value),
                duration: parseInt(form['service-duration'].value),
            }};

            showLoading(true);

            try {{
                const response = await fetch(url, {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(serviceData)
                }});

                const result = await response.json();

                if (!response.ok) throw new Error(result.message || 'Erro ao salvar serviço.');
                
                openModal('Sucesso', result.message, true);
                clearServiceForm();
                loadServicesList();
                
            }} catch (error) {{
                console.error("Erro ao salvar serviço:", error);
                openModal('Erro', `Não foi possível salvar o serviço. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}
        
        function editService(id, name, price, duration) {{
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem editar serviços.', false);
            document.getElementById('service-doc-id').value = id;
            document.getElementById('service-name').value = name;
            document.getElementById('service-price').value = parseFloat(price); 
            document.getElementById('service-duration').value = duration;
            document.getElementById('service-button-text').textContent = 'Salvar Edição';
            document.getElementById('cancel-service-button').classList.remove('hidden');
        }}

        function clearServiceForm() {{
            document.getElementById('service-doc-id').value = '';
            document.getElementById('service-name').value = '';
            document.getElementById('service-price').value = '';
            document.getElementById('service-duration').value = '';
            document.getElementById('service-button-text').textContent = 'Salvar Novo Serviço';
            document.getElementById('cancel-service-button').classList.add('hidden');
        }}

        async function deleteService(id, name) {{
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem excluir serviços.', false);
            
            showLoading(true);
            try {{
                const response = await fetch('/api/services', {{
                    method: 'DELETE',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ id: id }})
                }});

                const result = await response.json();
                if (!response.ok) throw new Error(result.message || 'Erro ao excluir.');

                openModal('Serviço Excluído', result.message, true);
                loadServicesList();
            }} catch (error) {{
                console.error("Erro ao excluir serviço:", error);
                openModal('Erro', `Não foi possível excluir o serviço. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}

        // --- Lógica de Despesas (Mantida) ---

        async function loadExpenses() {{
            if (userRole !== 'admin') return; 

            const expensesListEl = document.getElementById('current-expenses-list');
            expensesListEl.innerHTML = '<tr><td colspan="4" class="text-center text-gray-500 p-4">Carregando despesas...</td></tr>';
            
            try {{
                const response = await fetch('/api/expenses');
                if (!response.ok) throw new Error('Falha ao buscar despesas.');
                const expenses = await response.json();

                expensesListEl.innerHTML = ''; 
                let totalAmount = 0;
                
                if (expenses.length === 0) {{
                    expensesListEl.innerHTML = '<tr><td colspan="4" class="text-center text-gray-500 p-4">Nenhuma despesa registrada para este mês.</td></tr>';
                    return;
                }}

                expenses.forEach((expense) => {{
                    totalAmount += expense.amount;
                    const formattedAmount = expense.amount.toFixed(2).replace('.', ',');
                    
                    const listItem = `
                        <tr id="exp-${{expense.id}}">
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-gray-500">${{expense.expense_date}}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">${{expense.description}}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-sm text-red-600 font-bold">R$ ${{formattedAmount}}</td>
                            <td class="px-6 py-4 whitespace-nowrap text-right text-sm font-medium">
                                <button onclick="deleteExpense(${{expense.id}}, '${{expense.description.replace(/'/g, "\\'") }}')" class="text-red-500 hover:text-red-700 transition-colors duration-200">
                                    Excluir
                                </button>
                            </td>
                        </tr>
                    `;
                    expensesListEl.insertAdjacentHTML('beforeend', listItem);
                }});

                // Adicionar linha de total
                expensesListEl.insertAdjacentHTML('beforeend', `
                    <tr class="bg-gray-100 font-bold">
                        <td class="px-6 py-4 whitespace-nowrap text-base text-gray-900" colspan="2">TOTAL DESPESAS (MÊS)</td>
                        <td class="px-6 py-4 whitespace-nowrap text-base text-red-700">R$ ${{totalAmount.toFixed(2).replace('.', ',')}}</td>
                        <td class="px-6 py-4 whitespace-nowrap"></td>
                    </tr>
                `);
                
            }} catch (error) {{
                console.error("Erro ao carregar despesas:", error);
                openModal('Erro de Dados', error.message, false);
                expensesListEl.innerHTML = '<tr><td colspan="4" class="text-center text-red-500 p-4">Erro ao carregar despesas.</td></tr>';
            }}
        }}

        async function handleExpenseSubmit(event) {{
            event.preventDefault();
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem gerenciar despesas.', false);
            
            const form = event.target;
            
            const expenseData = {{
                description: form['expense-description'].value,
                amount: parseFloat(form['expense-amount'].value),
                date: form['expense-date'].value,
            }};

            showLoading(true);

            try {{
                const response = await fetch('/api/expenses', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(expenseData)
                }});

                const result = await response.json();

                if (!response.ok) throw new Error(result.message || 'Erro ao adicionar despesa.');
                
                openModal('Despesa Adicionada', result.message, true);
                form.reset();
                loadExpenses(); // Recarrega a lista
                loadCashFlow(); // Atualiza o dashboard
                
            }} catch (error) {{
                console.error("Erro ao adicionar despesa:", error);
                openModal('Erro', `Não foi possível adicionar a despesa. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}

        async function deleteExpense(id, description) {{
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem excluir despesas.', false);
            
            showLoading(true);
            try {{
                const response = await fetch(`/api/expenses/${{id}}`, {{
                    method: 'DELETE',
                }});

                const result = await response.json();
                if (!response.ok) throw new Error(result.message || 'Erro ao excluir.');

                openModal('Despesa Excluída', `Despesa "${{description}}" excluída com sucesso.`, true);
                loadExpenses(); // Recarrega a lista
                loadCashFlow(); // Atualiza o dashboard
            }} catch (error) {{
                console.error("Erro ao excluir despesa:", error);
                openModal('Erro', `Não foi possível excluir a despesa. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}

        // --- Inicialização da Aplicação (Mantida) ---
        window.onload = function() {{
            // Define a data atual no campo de despesa
            document.getElementById('expense-date').valueAsDate = new Date();

            // Checa se já existe uma sessão de usuário ao carregar a página
            fetch('/api/login', {{ method: 'GET' }})
                .then(response => response.json())
                .then(data => {{
                    if (data.role && data.role !== 'none') {{
                        userRole = data.role;
                        document.getElementById('login-view').classList.add('hidden');
                        document.getElementById('app-header').classList.remove('hidden');
                        if (userRole === 'admin') {{
                            document.getElementById('view-switch-container').classList.remove('hidden');
                            changeView('admin');
                        }} else {{
                            document.getElementById('view-switch-container').classList.add('hidden');
                            changeView('schedule');
                        }}
                    }} else {{
                        document.getElementById('login-view').classList.remove('hidden');
                        showLoading(false); 
                    }}
                }})
                .catch(() => {{
                    document.getElementById('login-view').classList.remove('hidden');
                    showLoading(false);
                }});
        }};


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
        window.archiveAppointment = archiveAppointment; // NOVO
        window.loadArchivedAppointments = loadArchivedAppointments; // NOVO
        
        window.handleRoleSelection = handleRoleSelection;
        window.handleAdminLogin = handleAdminLogin;
        window.logout = logout;

    </script>
</body>
</html>
"""

if __name__ == '__main__':
    app.run(debug=True)
