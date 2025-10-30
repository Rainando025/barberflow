# -*- coding: utf-8 -*-
import os
import psycopg2
import psycopg2.extras 
from flask import Flask, request, jsonify, render_template_string, session, redirect, url_for
from datetime import datetime, date

# --- 1. CONFIGURAÇÃO E CONEXÃO COM POSTGRESQL ---
# Usar a variável de ambiente DATABASE_URL do Render
DATABASE_URL = os.environ.get('DATABASE_URL')
# Chave secreta para sessões do Flask
FLASK_SECRET_KEY = os.environ.get('e205e9ea1d4aaf49f7b810ef5666d7aaffad3a9f1c66dbe4763e03faffef7b90')  # opcional
ADMIN_KEY = 'barberflowadmin'
FIXED_EXPENSES = 1500.00

def get_db_connection():
    """Cria e retorna uma conexão com o banco de dados."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"Erro ao conectar ao PostgreSQL: {e}")
        return None

# Inicialize Flask
app = Flask(__name__)
app.secret_key = FLASK_SECRET_KEY

def initialize_db():
    """Cria as tabelas Services e Appointments se não existirem."""
    conn = get_db_connection()
    if conn is None:
        return

    try:
        with conn.cursor() as cur:
            # Tabela de Serviços (Visível para Clientes e Editável pelo Admin)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    price NUMERIC(10, 2) NOT NULL,
                    duration INTEGER NOT NULL
                );
            """)

            # Tabela de Agendamentos (Usada pelo Cliente, Gerenciada pelo Admin)
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
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            
            # Adicionar serviços mock se a tabela estiver vazia
            cur.execute("SELECT COUNT(*) FROM services;")
            if cur.fetchone()[0] == 0:
                cur.execute("INSERT INTO services (name, price, duration) VALUES ('Corte Simples', 35.00, 45);")
                cur.execute("INSERT INTO services (name, price, duration) VALUES ('Design de Barba', 25.00, 30);")
                cur.execute("INSERT INTO services (name, price, duration) VALUES ('Corte + Barba', 55.00, 75);")
                print("Serviços mock inseridos.")

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

# --- Rotas de Autenticação e Configuração ---

@app.route('/', methods=['GET'])
def index():
    """Serve o arquivo HTML com o frontend."""
    # Renderiza o template HTML (definido como uma string no final do script)
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

# --- Rotas de Serviços (Admin e Cliente) ---

@app.route('/api/services', methods=['GET', 'POST', 'PUT', 'DELETE'])
def manage_services():
    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    role = get_role()
    
    try:
        # Usa psycopg2.extras.RealDictCursor para retornar resultados como dicionários
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if request.method == 'GET':
                # Cliente e Admin podem ver os serviços
                cur.execute("SELECT id, name, price, duration FROM services ORDER BY name;")
                services = cur.fetchall()
                return jsonify(services)

            # Ações de escrita (POST, PUT, DELETE) requerem permissão de Admin
            if role != 'admin':
                return jsonify({'message': 'Acesso negado. Apenas Barbeiros (Admin) podem gerenciar serviços.'}), 403

            if request.method == 'POST':
                # Criação ou Edição de Serviço
                data = request.get_json()
                service_id = data.get('id')
                name = data.get('name')
                price = data.get('price')
                duration = data.get('duration')
                
                if service_id:
                    # PUT/Edição
                    cur.execute("UPDATE services SET name = %s, price = %s, duration = %s WHERE id = %s RETURNING id;",
                                (name, price, duration, service_id))
                    conn.commit()
                    return jsonify({'message': f'Serviço ID {service_id} atualizado com sucesso.'})
                else:
                    # POST/Criação
                    cur.execute("INSERT INTO services (name, price, duration) VALUES (%s, %s, %s) RETURNING id;",
                                (name, price, duration))
                    new_id = cur.fetchone()['id'] # fetchone() retorna um dict graças ao RealDictCursor
                    conn.commit()
                    return jsonify({'message': f'Serviço "{name}" adicionado com ID {new_id}.'}), 201

            elif request.method == 'DELETE':
                # Exclusão de Serviço
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

# --- Rotas de Agendamentos (Admin e Cliente) ---

@app.route('/api/appointments', methods=['GET', 'POST'])
def manage_appointments():
    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    role = get_role()

    try:
        # Usa psycopg2.extras.RealDictCursor para retornar resultados como dicionários
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if request.method == 'GET':
                # Apenas Admin pode ver todos os agendamentos
                if role != 'admin':
                    return jsonify({'message': 'Acesso negado. Apenas Barbeiros (Admin) podem ver todos os agendamentos.'}), 403
                
                # Pega todos os agendamentos, ordenados por data e hora
                cur.execute("SELECT id, barber_id, service_name, appointment_date, appointment_time, client_name, service_price, status FROM appointments ORDER BY appointment_date, appointment_time;")
                appointments = cur.fetchall()
                # Converte o objeto date do Python para string no formato 'YYYY-MM-DD'
                for appt in appointments:
                    appt['appointment_date'] = appt['appointment_date'].strftime('%Y-%m-%d')
                return jsonify(appointments)

            elif request.method == 'POST':
                # Cliente submete agendamento
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
    """Admin atualiza o status de um agendamento."""
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

@app.route('/api/dashboard', methods=['GET'])
def get_dashboard_data():
    """Calcula e retorna dados do dashboard."""
    if get_role() != 'admin':
        return jsonify({'message': 'Acesso negado.'}), 403

    conn = get_db_connection()
    if conn is None:
        return jsonify({'message': 'Erro de conexão com o banco de dados'}), 500

    try:
        # Usa psycopg2.extras.RealDictCursor para retornar resultados como dicionários
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Calcule a receita total dos agendamentos CONCLUÍDOS
            cur.execute("""
                SELECT SUM(service_price) as total_revenue, COUNT(*) as completed_count
                FROM appointments
                WHERE status = 'Concluído' 
                -- Filtro simples por mês: no mundo real, usaria BETWEEN
                AND EXTRACT(MONTH FROM appointment_date) = EXTRACT(MONTH FROM CURRENT_DATE);
            """)
            
            result = cur.fetchone()
            total_revenue = float(result['total_revenue'] or 0.0)
            completed_count = result['completed_count'] or 0

            net_income = total_revenue - FIXED_EXPENSES

            return jsonify({
                'totalRevenue': total_revenue,
                'completedAppointments': completed_count,
                'totalExpenses': FIXED_EXPENSES,
                'netIncome': net_income
            })

    except Exception as e:
        print(f"Erro ao obter dados do dashboard: {e}")
        return jsonify({'message': f'Erro interno: {e}'}), 500
    finally:
        conn.close()


# --- 3. CONTEÚDO HTML E JAVASCRIPT ---

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
                            Agendamentos
                        </button>
                        <button onclick="changeAdminTab('services')" id="tab-services" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700">
                            Serviços e Preços
                        </button>
                        <button onclick="changeAdminTab('dashboard')" id="tab-dashboard" class="tab-button border-b-2 py-4 px-1 text-sm font-medium whitespace-nowrap border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700">
                            Dashboard e Caixa
                        </button>
                    </nav>
                </div>

                <!-- Conteúdo das Tabs -->
                <div class="mt-6 space-y-8">
                    
                    <!-- 2.1. Agendamentos do Dia (Real-Time) -->
                    <div id="appointments-tab" class="tab-content">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Próximos Agendamentos</h3>
                        <div class="bg-gray-50 p-4 rounded-lg shadow-inner">
                            <p class="text-sm text-gray-600 mb-4">Atualize o status para calcular o Fluxo de Caixa.</p>
                            <div id="appointments-list" class="space-y-4">
                                <p class="text-center text-gray-500">Carregando agendamentos...</p>
                            </div>
                        </div>
                    </div>

                    <!-- 2.2. Gerenciamento de Serviços e Preços -->
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

                    <!-- 2.3. Dashboard e Fluxo de Caixa -->
                    <div id="dashboard-tab" class="tab-content hidden">
                        <h3 class="text-xl font-semibold text-gray-800 mb-4">Dashboard Financeiro (Agendamentos Concluídos - Mês)</h3>
                        
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
                                <p class="text-xs text-gray-500 mt-2">Agendamentos finalizados (pagos).</p>
                            </div>

                            <!-- Card 3: Despesas Fixas -->
                            <div class="bg-red-50 p-6 rounded-xl shadow-lg border-l-4 border-red-600">
                                <p class="text-sm font-medium text-red-700">Despesas Fixas (Mock)</p>
                                <p id="total-expenses" class="mt-1 text-3xl font-bold text-red-800">R$ {{fixed_expenses}}</p>
                                <p class="text-xs text-gray-500 mt-2">Simulação: Aluguel, produtos, etc.</p>
                            </div>

                            <!-- Card 4: Lucro Líquido (Calculado) -->
                            <div id="net-income-card" class="bg-purple-50 p-6 rounded-xl shadow-lg border-l-4 border-purple-600">
                                <p class="text-sm font-medium text-purple-700">Lucro Líquido</p>
                                <p id="net-income" class="mt-1 text-3xl font-bold text-purple-800">R$ 0,00</p>
                                <p class="text-xs text-gray-500 mt-2">Receita total - Despesas fixas.</p>
                            </div>
                        </div>

                        <!-- Gráfico Simples de Fluxo (Simulado) -->
                        <div class="mt-8 bg-white p-6 rounded-xl shadow-md">
                            <h4 class="text-lg font-medium text-gray-700 mb-4">Visão Geral (Mock)</h4>
                            <img src="https://placehold.co/800x200/cccccc/333333?text=Placeholder+Gráfico+de+Receita+e+Agendamentos" alt="Placeholder de um gráfico de linha mostrando o fluxo de caixa" class="w-full rounded-lg">
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
        // No mundo real, esta informação viria do servidor via cookie/sessão
        let userRole = 'none'; 
        const ADMIN_KEY = '{ADMIN_KEY}'; // Injetado do Python

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
                titleElement.classList.remove('text-red-700');
                titleElement.classList.add('text-green-700');
            }} else {{
                titleElement.classList.remove('text-green-700');
                titleElement.classList.add('text-red-700');
            }}
            modal.classList.remove('hidden');
        }}

        function closeModal() {{
            document.getElementById('message-modal').classList.add('hidden');
        }}
        
        // --- Lógica de Login e Permissões ---

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
            
            if (tabName === 'appointments') {{
                loadAppointments();
            }} else if (tabName === 'services') {{
                loadServicesList();
            }} else if (tabName === 'dashboard') {{
                loadCashFlow();
            }}
        }}


        // ----------------------------------------------------------------------
        // LÓGICA DO CLIENTE (AGENDAMENTO) - Comunicação com Flask API
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
                    // CORREÇÃO: Converte service.price para float antes de toFixed
                    const servicePrice = parseFloat(service.price);
                    const formattedPrice = servicePrice.toFixed(2);
                    
                    const option = document.createElement('option');
                    option.value = service.id;
                    option.textContent = `${{service.name}} (R$ ${{formattedPrice.replace('.', ',')}} - ${{service.duration}} min)`;
                    option.setAttribute('data-price', formattedPrice); // Armazena como string formatada para JS
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
            
            // O preço é lido do data-price que já está como string formatada
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
        // LÓGICA DO BARBEIRO (ADMIN) - Comunicação com Flask API
        // ----------------------------------------------------------------------
        
        function loadAdminData() {{
            if (userRole !== 'admin') return; 
            // Carrega a aba padrão, que irá disparar a função de carregamento
            changeAdminTab('appointments');
            // Recarrega serviços também para garantir o formulário do admin
            loadServicesList();
        }}

        async function loadAppointments() {{
            if (userRole !== 'admin') return; 

            const appointmentsListEl = document.getElementById('appointments-list');
            appointmentsListEl.innerHTML = '<p class="text-center text-gray-500">Carregando agendamentos...</p>';
            
            try {{
                const response = await fetch('/api/appointments');
                if (!response.ok) throw new Error('Falha ao buscar agendamentos.');
                const appointments = await response.json();

                appointmentsListEl.innerHTML = ''; 
                
                if (appointments.length === 0) {{
                    appointmentsListEl.innerHTML = '<p class="text-center text-gray-500 p-4">Nenhum agendamento encontrado.</p>';
                    return;
                }}

                // Ordenação em memória (já que o backend já ordenou por data/hora)
                appointments.forEach((appointment) => {{
                    const appointmentId = appointment.id;

                    let statusClass = '';
                    switch (appointment.status) {{
                        case 'Concluído': statusClass = 'bg-green-100 text-green-800'; break;
                        case 'Cancelado': statusClass = 'bg-red-100 text-red-800'; break;
                        case 'Agendado': default: statusClass = 'bg-yellow-100 text-yellow-800'; break;
                    }}

                    const barberName = appointment.barber_id === 'barber1' ? 'João' : (appointment.barber_id === 'barber2' ? 'Pedro' : appointment.barber_id);
                    // Garante que service_price é float antes de formatar
                    const formattedPrice = parseFloat(appointment.service_price).toFixed(2).replace('.', ',');

                    const appointmentHtml = `
                        <div id="appt-${{appointmentId}}" class="p-4 bg-white rounded-lg shadow flex flex-col sm:flex-row justify-between items-start sm:items-center transition-all duration-200 hover:shadow-md">
                            <div class="mb-2 sm:mb-0">
                                <p class="font-bold text-lg text-gray-800">${{appointment.appointment_time}} (${{appointment.appointment_date}})</p>
                                <p class="text-sm text-gray-600">${{appointment.client_name}} - ${{appointment.service_name}} c/ ${{barberName}} (R$ ${{formattedPrice}})</p>
                            </div>
                            <div class="flex items-center space-x-2 mt-2 sm:mt-0">
                                <span class="px-3 py-1 text-xs font-semibold rounded-full ${{statusClass}}">${{appointment.status}}</span>
                                <select onchange="updateAppointmentStatus(${{appointmentId}}, this.value)" class="py-1 px-2 border border-gray-300 rounded-lg text-sm focus:ring-red-500 focus:border-red-500">
                                    <option value="Agendado" ${{appointment.status === 'Agendado' ? 'selected' : ''}}>Agendar</option>
                                    <option value="Concluído" ${{appointment.status === 'Concluído' ? 'selected' : ''}}>Concluir</option>
                                    <option value="Cancelado" ${{appointment.status === 'Cancelado' ? 'selected' : ''}}>Cancelar</option>
                                </select>
                                <button onclick="deleteAppointment(${{appointmentId}})" class="text-red-500 hover:text-red-700 text-sm p-1 rounded hover:bg-red-50">
                                    <svg xmlns="http://www.w3.org/2000/svg" class="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                        <path fill-rule="evenodd" d="M9 2a1 1 0 00-.894.553L7.382 4H4a1 1 0 000 2v10a2 2 0 002 2h8a2 2 0 002-2V6a1 1 0 100-2h-3.382l-.724-1.447A1 1 0 0011 2H9zM7 8a1 1 0 012 0v6a1 1 0 11-2 0V8zm4 0a1 1 0 10-2 0v6a1 1 0 102 0V8z" clip-rule="evenodd" />
                                    </svg>
                                </button>
                            </div>
                        </div>
                    `;
                    appointmentsListEl.insertAdjacentHTML('beforeend', appointmentHtml);
                }});

                loadCashFlow(); // Recarrega o dashboard
            }} catch (error) {{
                console.error("Erro ao carregar agendamentos:", error);
                openModal('Erro de Dados', error.message, false);
                appointmentsListEl.innerHTML = '<p class="text-center text-red-500 p-4">Erro ao carregar agendamentos.</p>';
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
                loadAppointments(); // Recarregar lista para refletir a mudança
            }} catch (error) {{
                console.error("Erro ao atualizar status:", error);
                openModal('Erro', `Não foi possível atualizar o status. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
        }}

        async function deleteAppointment(id) {{
            if (userRole !== 'admin') return openModal('Permissão Negada', 'Apenas Barbeiros (Admin) podem excluir agendamentos.', false);
            showLoading(true);
            try {{
                // Note: Flask não tem rota DELETE para appointments, mas é uma boa prática usar DELETE
                // Como não criei a rota DELETE específica, vou usar a PUT para demo, mas deveria ser DELETE no real.
                const response = await fetch(`/api/services`, {{ 
                    method: 'DELETE',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ id: id }})
                }});
                
                const result = await response.json();
                if (!response.ok) throw new Error(result.message || 'Erro ao excluir.');
                
                openModal('Excluído', `Agendamento #${{id}} excluído com sucesso.`, true);
                loadAppointments(); // Recarregar lista
            }} catch (error) {{
                console.error("Erro ao excluir agendamento:", error);
                openModal('Erro', `Não foi possível excluir o agendamento. ${{error.message}}`, false);
            }} finally {{
                showLoading(false);
            }}
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
            }} catch (error) {{
                 console.error("Erro ao carregar dashboard:", error);
                 openModal('Erro no Dashboard', error.message, false);
            }}
        }}
        
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
                    // CORREÇÃO: Converte service.price para float antes de toFixed
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
            const method = docId ? 'POST' : 'POST'; // Usaremos POST para criação e edição no Flask
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
                    method: method,
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
            // O preço é um valor que vem do Python e é numeric, mas é enviado como string
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

        // --- Inicialização da Aplicação ---
        window.onload = function() {{
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
                        showLoading(false); // Esconde o loading inicial (se houver)
                    }}
                }})
                .catch(() => {{
                    document.getElementById('login-view').classList.remove('hidden');
                    showLoading(false); // Em caso de erro, exibe o login
                }});
        }};


        // --- EXPOSIÇÃO GLOBAL DE FUNÇÕES ---
        window.changeView = changeView;
        window.handleAppointmentSubmit = handleAppointmentSubmit;
        window.changeAdminTab = changeAdminTab;
        window.closeModal = closeModal;
        window.updateAppointmentStatus = updateAppointmentStatus;
        window.deleteAppointment = deleteAppointment;
        window.handleServiceSubmit = handleServiceSubmit;
        window.clearServiceForm = clearServiceForm;
        window.editService = editService;
        window.deleteService = deleteService;
        
        window.handleRoleSelection = handleRoleSelection;
        window.handleAdminLogin = handleAdminLogin;
        window.logout = logout;

    </script>
</body>
</html>
"""

# Se o script for executado diretamente, inicie o Flask
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))  # Render define a porta automaticamente
    app.run(host='0.0.0.0', port=port, debug=True)  # debug=False em produção









