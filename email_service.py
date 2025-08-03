import re
import os
import datetime
import secrets
from flask import current_app, url_for, render_template_string
from flask_mail import Message
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadData

# Validate email format
def is_valid_email(email: str) -> bool:
    pattern = r'^[\w\.-]+@[\w\.-]+\\.\w+$'
    return re.match(pattern, email) is not None

# Generate and confirm email tokens
def generate_confirmation_token(email: str) -> str:
    ts = URLSafeTimedSerializer(current_app.config['SECRET_SEND_KEY'])
    return ts.dumps(email, salt='email-confirm')

def confirm_token(token: str, expiration: int = 3600) -> str:
    ts = URLSafeTimedSerializer(current_app.config['SECRET_SEND_KEY'])
    return ts.loads(token, salt='email-confirm', max_age=expiration)

# Send confirmation email

def send_confirmation_email(email: str):
    token = generate_confirmation_token(email)
    confirm_url = url_for('confirm_email', token=token, _external=True)
    html = render_template_string(
        "<p>Для подтверждения почты перейдите по ссылке:</p>"
        "<a href='{{ url }}'>{{ url }}</a>",
        url=confirm_url
    )
    msg = Message(
        subject="Подтвердите вашу почту",
        sender=current_app.config['MAIL_USERNAME'],
        recipients=[email],
        html=html
    )
    mail = current_app.mail
    mail.send(msg)
