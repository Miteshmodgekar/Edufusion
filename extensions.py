"""
Flask extensions — defined here to avoid circular imports.
Import db, bcrypt, login_manager from this file in all models and routes.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_bcrypt import Bcrypt

db            = SQLAlchemy()
login_manager = LoginManager()
bcrypt        = Bcrypt()

try:
    from flask_mail import Mail
    mail = Mail()
    MAIL_AVAILABLE = True
except ImportError:
    mail = None
    MAIL_AVAILABLE = False
