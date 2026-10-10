WLADIMIR UNIVERSITY PORTAL (Flask + MySQL)
1. sudo apt install python3-flask python3-pymysql
2. sudo mysql < setup_mysql.sql        (creates DB wladimir_portal + user wladimir)
3. python3 app.py                       -> http://localhost:5000
Tables are created automatically on first run.
Custom MySQL settings: export DB_HOST DB_USER DB_PASSWORD DB_NAME before running.
Logins: superadmin / admin123 (change it), student1 / student123 (demo)
