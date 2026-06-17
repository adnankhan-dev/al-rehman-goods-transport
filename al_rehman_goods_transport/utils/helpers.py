# Utility functions for the application

def format_currency(amount):
    """Format a number as currency"""
    return f"RS{amount:,.2f}"

def format_quantity(quantity):
    """Format quantity with appropriate units"""
    return f"{quantity:,.2f} CFT"