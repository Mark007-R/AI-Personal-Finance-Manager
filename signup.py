from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session
from werkzeug.security import generate_password_hash

from database import get_db_connection

signup_bp = Blueprint('signup_bp', __name__)

MIN_PASSWORD = 8


@signup_bp.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm-password', '').strip()

        if not name or not email or not password or not confirm_password:
            return jsonify({'success': False, 'message': 'Fill in every field.'})

        if len(name) > 120:
            return jsonify({'success': False, 'message': 'Use a shorter name (120 characters max).'})

        if '@' not in email or '.' not in email:
            return jsonify({'success': False, 'message': "That email address doesn't look right."})

        if len(password) < MIN_PASSWORD:
            return jsonify({'success': False,
                            'message': f'Use at least {MIN_PASSWORD} characters for your password.'})

        if password != confirm_password:
            return jsonify({'success': False, 'message': "The passwords don't match."})

        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM users1 WHERE email = %s", (email,))
                if cursor.fetchone():
                    return jsonify({'success': False,
                                    'message': 'An account already uses that email. Try signing in.'})

                hashed_password = generate_password_hash(password)
                cursor.execute(
                    "INSERT INTO users1 (firstname, lastname, email, password) VALUES (%s, %s, %s, %s)",
                    (name, '', email, hashed_password)
                )
                conn.commit()

                # sign the new user straight in
                session.clear()
                session['user_id'] = cursor.lastrowid
                session['email'] = email
                session['name'] = name
                return jsonify({'success': True, 'redirect': url_for('overview')})
        except Exception as e:
            print("Signup error:", str(e))
            return jsonify({'success': False,
                            'message': "We couldn't create your account just now. Please try again."})
        finally:
            if conn is not None:
                conn.close()

    if 'user_id' in session:
        return redirect(url_for('overview'))
    return render_template('signup.html')
