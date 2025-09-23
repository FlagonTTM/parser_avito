# Avito Parser - Modified Console Version

Modified version of the Avito parser according to specific requirements.

## 🎯 Key Changes Made

### ❌ Removed Features
- **Telegram notifications** - No bot integration, no message sending
- **Price change tracking** - Simplified database (only tracks processed item IDs)  
- **GUI interface** - Console-only operation (original GUI still available)

### ✅ Added Features
- **PostgreSQL support** - Export to PostgreSQL in addition to SQLite
- **Local IP switching** - Option to work with local IP instead of proxy
- **Configurable proxy usage** - Enable/disable proxy usage via config
- **Console interface** - Simple command-line operation with options
- **Improved configuration** - Better control over all features

## 🚀 Quick Start

### 1. Installation
```bash
# Install base dependencies
pip install -r requirements.txt

# For PostgreSQL support (optional)
pip install psycopg2-binary
```

### 2. Configuration
Edit `config.toml`:
```toml
[avito]
# Basic settings
urls = ["https://www.avito.ru/your-search-url"]

# Database (choose one)
database_type = "sqlite"          # Local SQLite file
# database_type = "postgresql"    # PostgreSQL server
# database_url = "postgresql://user:pass@host:5432/dbname"

# IP Management (choose one mode)
use_local_ip = true               # Use local IP with switching simulation
use_proxy = false                 # Disable proxy
# use_proxy = true                # Enable proxy mode
# proxy_string = "user:pass@proxy:port"
# proxy_change_url = "https://api.proxy.com/change-ip"
```

### 3. Usage
```bash
# Run once and exit
python console_parser.py --once

# Run continuously 
python console_parser.py

# Custom config file
python console_parser.py --config my_config.toml

# Verbose logging
python console_parser.py --verbose
```

## 📊 Database Options

### SQLite (Default)
```toml
database_type = "sqlite"
```
- Data stored in local `database.db` file
- No setup required
- Perfect for single-user scenarios

### PostgreSQL  
```toml
database_type = "postgresql"
database_url = "postgresql://username:password@localhost:5432/avito_parser"
```
- Requires PostgreSQL server
- Better for multi-user or production environments
- See `config_postgresql_example.toml` for full example

## 🌐 IP/Proxy Configuration

### Mode 1: Local IP (Recommended)
```toml
use_proxy = false
use_local_ip = true
```
- Uses your local internet connection
- Simulates IP changes with delays
- No proxy costs

### Mode 2: Proxy Usage
```toml
use_proxy = true
use_local_ip = false
proxy_string = "username:password@proxy.server:port"
proxy_change_url = "https://api.proxy.com/change-ip?key=..."
```
- Uses external proxy service
- Real IP changes via API
- Requires proxy subscription

### Mode 3: Direct Connection
```toml
use_proxy = false
use_local_ip = false
```
- Direct connection only
- No IP management
- May get blocked faster

## 📁 Output Files

- **Excel**: `result/*.xlsx` - Parsed ad data
- **Database**: Item IDs to prevent re-processing
- **Logs**: `logs/app.log` and console output

## 🔄 Migration from Original

The modified version is **backward compatible**:

- ✅ Existing `config.toml` files work (new settings optional)
- ✅ Original launchers still work:
  - `python AvitoParser.py` (GUI version)  
  - `python parser_cls.py` (original CLI)
- ❌ Telegram settings are ignored (no errors)
- ❌ Price tracking disabled (database simplified)

## 🖥️ Cross-Platform Support

Works on:
- ✅ Windows  
- ✅ Linux
- ✅ macOS

## ⚙️ Advanced Configuration

All original filtering options remain available:
- Price range filtering
- Keyword white/black lists
- Seller black lists  
- Geographic filtering
- Age-based filtering
- Promotion/reservation filtering

See `config.toml` for complete options.