# Развертывание на сервере (Linux)
Данная инструкция основана на реальном опыте настройки сервера и включает все необходимые шаги для запуска бота в производственной среде. 
```Данное описание создано ИИ на базе истории консоли. Возможно, чего-то будет нехватать. :)```

## 1. Подготовка системы
Обновите пакеты и установите базовые утилиты:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y mc curl wget
```

## 2. Установка и настройка MySQL
Установите сервер MySQL и выполните первоначальную настройку:

```bash
sudo apt install -y mysql-server
sudo systemctl start mysql
sudo mysql_secure_installation
```

Создайте базу данных и пользователя для бота (вместо yourpassword укажите надёжный пароль):

```bash
sudo mysql -u root
```
В интерактивном режиме MySQL выполните:

```sql
CREATE DATABASE diabetes_bot;
CREATE USER 'bot_user'@'localhost' IDENTIFIED BY 'yourpassword';
GRANT ALL PRIVILEGES ON diabetes_bot.* TO 'bot_user'@'localhost';
FLUSH PRIVILEGES;
EXIT;
```

## 3. Настройка фаервола (iptables)
Для безопасности ограничьте входящий трафик. Разрешите только необходимое:

```bash
# Разрешить локальный трафик
sudo iptables -A INPUT -i lo -j ACCEPT
# Разрешить ответы на установленные соединения
sudo iptables -A INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
# Разрешить SSH (порт 22)
sudo iptables -A INPUT -p tcp --dport 22 -j ACCEPT
# Если планируется веб-сервер, разрешить 80 и 443
sudo iptables -A INPUT -p tcp --dport 80 -j ACCEPT
sudo iptables -A INPUT -p tcp --dport 443 -j ACCEPT
# Разрешить доступ с вашего IP (замените на свой)
sudo iptables -A INPUT -s ваш.IP.адрес -j ACCEPT
# Установить политику по умолчанию DROP для INPUT
sudo iptables -P INPUT DROP
```

Сохраните правила, чтобы они восстановились после перезагрузки:

```bash
sudo apt install -y iptables-persistent
sudo iptables-save > /etc/iptables/rules.v4
```

## 4. Установка Python и создание виртуального окружения
Установите Python и pip:

```bash
sudo apt install -y python3 python3-pip python3-venv
```
Создайте виртуальное окружение для бота:

```bash
python3 -m venv ~/py_envs
source ~/py_envs/bin/activate
```
Установите необходимые библиотеки:

```bash
pip install pyTelegramBotAPI mysql-connector-python pandas matplotlib openpyxl python-dotenv
```
## 5. Размещение файлов бота
Создайте директорию для бота и перейдите в неё:

```bash
mkdir ~/bot
cd ~/bot
```
Поместите туда файл bot_v10.py (скопируйте содержимое через nano или mc). Также создайте файл .env с конфигурацией:

```bash
nano .env
```
Пример содержимого .env:

```text
BOT_TOKEN=ваш_токен_бота
DB_USER=bot_user
DB_PASSWORD=yourpassword
DB_HOST=localhost
DB_NAME=diabetes_bot
```

## 6. Запуск бота через systemd (автозапуск)
Создайте unit-файл systemd:

```bash
sudo nano /etc/systemd/system/bot.service
```
Вставьте следующее (измените пути при необходимости):

```text
[Unit]
Description=Diabetes Bot
After=network.target mysql.service

[Service]
User=your_username
WorkingDirectory=/home/your_username/bot
Environment="PATH=/home/your_username/py_envs/bin"
ExecStart=/home/your_username/py_envs/bin/python /home/your_username/bot/bot10.py
Restart=always

[Install]
WantedBy=multi-user.target
```
Перезагрузите systemd, включите и запустите сервис:

```bash
sudo systemctl daemon-reload
sudo systemctl enable bot.service
sudo systemctl start bot.service
```
Проверьте статус:

```bash
sudo systemctl status bot.service
```
## 7. Проверка работы
Убедитесь, что бот отвечает в Telegram. Логи можно посмотреть командой:

```bash
sudo journalctl -u bot.service -f
```

## Примечания

Если вы используете другой пользователь, замените your_username на актуальное имя.

Для корректной работы MySQL убедитесь, что сервис mysql запущен до бота (указано After=mysql.service).

Правила iptables можно в любой момент просмотреть командой sudo iptables -L -n -v.

