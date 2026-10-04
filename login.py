from flask import Blueprint, request, jsonify, render_template, redirect, url_for, session
from werkzeug.security import check_password_hash

from database import get_db_connection

login_bp = Blueprint('login_bp', __name__)


@login_bp.route('/login', methods=['GET', 'POST'], endpoint='login')
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()

        if not email or not password:
            return jsonify({'success': False, 'message': 'Enter your email and password.'})

        if '@' not in email or '.' not in email:
            return jsonify({'success': False, 'message': "That email address doesn't look right."})

        conn = None
        try:
            conn = get_db_connection()
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, firstname, password FROM users1 WHERE email = %s", (email,))
                user = cursor.fetchone()

                if not user:
                    return jsonify({'success': False, 'message': 'No account uses that email yet.'})

                if not check_password_hash(user['password'], password):
                    return jsonify({'success': False, 'message': "That password isn't right."})

                session.clear()  # fresh session (and CSRF token) on sign-in
                session['user_id'] = user['id']
                session['email'] = email
                session['name'] = user['firstname']
                return jsonify({'success': True, 'redirect': url_for('overview')})
        except Exception as e:
            print('Login error:', e)
            return jsonify({'success': False,
                            'message': "We couldn't sign you in just now. Please try again."})
        finally:
            if conn is not None:
                conn.close()

    if 'user_id' in session:
        return redirect(url_for('overview'))
    return render_template('login.html')
