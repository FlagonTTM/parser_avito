#!/usr/bin/env python3
"""
Console-only version of Avito Parser
No GUI, no Telegram notifications, just parsing to database and Excel
"""
import argparse
import time
from pathlib import Path

from loguru import logger
from load_config import load_avito_config
from parser_cls import AvitoParse


def main():
    parser = argparse.ArgumentParser(description='Avito Parser - Console Version')
    parser.add_argument('--config', '-c', default='config.toml', 
                       help='Path to configuration file (default: config.toml)')
    parser.add_argument('--once', '-o', action='store_true',
                       help='Run once and exit (don\'t loop)')
    parser.add_argument('--verbose', '-v', action='store_true',
                       help='Enable verbose logging')
    
    args = parser.parse_args()
    
    if args.verbose:
        logger.add(lambda msg: print(msg, end=""), level="DEBUG")
    
    config_path = Path(args.config)
    if not config_path.exists():
        logger.error(f"Configuration file not found: {config_path}")
        exit(1)
    
    try:
        config = load_avito_config(str(config_path))
        logger.info(f"Loaded configuration from: {config_path}")
    except Exception as err:
        logger.error(f"Error loading config: {err}")
        exit(1)
    
    logger.info("=== Avito Job Parser Console Version ===")
    logger.info("Features:")
    logger.info("  ✓ No GUI - Console only")
    logger.info("  ✓ Job listings focused parsing")
    logger.info("  ✓ Date range filtering support")
    logger.info("  ✓ Detailed job information extraction (MVP)")
    logger.info("  ✓ No Telegram notifications")
    logger.info("  ✓ PostgreSQL support available")
    logger.info("  ✓ Configurable proxy/local IP switching")
    logger.info("=" * 40)
    
    if args.once:
        logger.info("Running once and exiting...")
        try:
            parser_instance = AvitoParse(config)
            parser_instance.parse()
            logger.info("Parsing completed successfully")
        except Exception as err:
            logger.error(f"Error during parsing: {err}")
            exit(1)
    else:
        logger.info("Running in continuous mode. Press Ctrl+C to stop.")
        while True:
            try:
                parser_instance = AvitoParse(config)
                parser_instance.parse()
                logger.info(f"Parsing completed. Sleeping for {config.pause_general} seconds")
                time.sleep(config.pause_general)
            except KeyboardInterrupt:
                logger.info("Interrupted by user. Exiting...")
                break
            except Exception as err:
                logger.error(f"Error during parsing: {err}")
                logger.info("Sleeping for 30 seconds before retry...")
                time.sleep(30)


if __name__ == "__main__":
    main()