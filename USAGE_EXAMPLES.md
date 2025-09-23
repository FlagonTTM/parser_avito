# Usage Examples

## Console Parser Examples

### Basic Usage
```bash
# Run once and exit (good for testing)
python console_parser.py --once

# Run continuously (production mode)
python console_parser.py

# Verbose logging for debugging
python console_parser.py --verbose --once
```

### Custom Configuration
```bash
# Use different config file
python console_parser.py --config my_custom_config.toml

# PostgreSQL example
python console_parser.py --config config_postgresql_example.toml --once
```

## Configuration Examples

### 1. Local IP Mode (Default)
```toml
[avito]
urls = ["https://www.avito.ru/your-search-url"]
database_type = "sqlite"
use_local_ip = true
use_proxy = false
pause_general = 60
```

### 2. Proxy Mode
```toml
[avito]
urls = ["https://www.avito.ru/your-search-url"]
database_type = "sqlite"
use_proxy = true
use_local_ip = false
proxy_string = "username:password@proxy.example.com:8080"
proxy_change_url = "https://api.proxy.com/change-ip?key=your-key"
```

### 3. PostgreSQL Database
```toml
[avito]
urls = ["https://www.avito.ru/your-search-url"]
database_type = "postgresql"
database_url = "postgresql://avito_user:password@localhost:5432/avito_parser"
use_local_ip = true
use_proxy = false
```

### 4. Advanced Filtering
```toml
[avito]
urls = ["https://www.avito.ru/your-search-url"]
keys_word_white_list = ["трактор", "новый"]
keys_word_black_list = ["битый", "требует ремонта"] 
seller_black_list = ["bad_seller_123"]
min_price = 100000
max_price = 5000000
geo = "Москва"
max_age = 3600  # Only ads from last hour
ignore_reserv = true
ignore_promotion = false
```

## Comparison with Original

### Old Way (with Telegram)
```bash
# Original GUI version
python AvitoParser.py

# Original CLI version
python parser_cls.py
```

### New Way (Console Only)
```bash
# New console version - no Telegram, no price tracking
python console_parser.py --once
```

## Database Setup

### SQLite (No setup needed)
Data automatically stored in `database.db` file.

### PostgreSQL Setup
```sql
-- Create database and user
CREATE DATABASE avito_parser;
CREATE USER avito_user WITH PASSWORD 'your_password';
GRANT ALL PRIVILEGES ON DATABASE avito_parser TO avito_user;

-- Connect to database
\c avito_parser

-- Table will be created automatically by the application
-- CREATE TABLE IF NOT EXISTS viewed (id BIGINT PRIMARY KEY);
```

## Output Examples

### Console Output
```
2025-09-23 19:02:09 | INFO | === Avito Parser Console Version ===
2025-09-23 19:02:09 | INFO | Features:
2025-09-23 19:02:09 | INFO |   ✓ No GUI - Console only
2025-09-23 19:02:09 | INFO |   ✓ No Telegram notifications  
2025-09-23 19:02:09 | INFO |   ✓ No price change tracking
2025-09-23 19:02:09 | INFO |   ✓ PostgreSQL support available
2025-09-23 19:02:09 | INFO |   ✓ Configurable proxy/local IP switching
2025-09-23 19:02:09 | INFO | Running once and exiting...
2025-09-23 19:02:09 | INFO | Работаем с локальным IP
2025-09-23 19:02:09 | INFO | Запуск AvitoParse v3.0.9
```

### File Structure After Running
```
project/
├── console_parser.py          # New console interface
├── config.toml               # Updated configuration  
├── database.db              # SQLite database (simplified)
├── result/                  # Excel output files
│   └── all.xlsx
├── logs/                    # Log files
│   └── app.log
└── cookies.json            # Session cookies
```