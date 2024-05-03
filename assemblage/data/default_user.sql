ALTER USER 'root'@'localhost' IDENTIFIED BY 'assemblage';
CREATE USER 'assemblage'@'%' IDENTIFIED BY 'assemblage';
GRANT ALL PRIVILEGES ON *.* TO 'assemblage'@'%' WITH GRANT OPTION;
CREATE DATABASE IF NOT EXISTS assemblage;
