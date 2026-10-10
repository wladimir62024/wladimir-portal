-- run once:  sudo mysql < setup_mysql.sql
CREATE DATABASE IF NOT EXISTS wladimir_portal CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'wladimir'@'localhost' IDENTIFIED BY 'StrongPass123!';
GRANT ALL PRIVILEGES ON wladimir_portal.* TO 'wladimir'@'localhost';
FLUSH PRIVILEGES;
