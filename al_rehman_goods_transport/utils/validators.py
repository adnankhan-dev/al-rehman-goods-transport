# Validation functions for the application

def validate_phone_number(phone):
    """Validate phone number format"""
    # Simple validation - can be enhanced
    return len(phone) >= 10 and phone.isdigit()

def validate_email(email):
    """Validate email format"""
    # Simple validation - can be enhanced
    return '@' in email and '.' in email