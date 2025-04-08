from flask import Flask, request, redirect, render_template, make_response, jsonify
import pymysql
import bcrypt
import datetime
import random
import string
import threading
from time import sleep
app = Flask(__name__)

# MySQL connection setup
db = pymysql.connect(
    host="localhost",
    user="root",
    password="@Mysql",
    database="mobilebanking",
    cursorclass=pymysql.cursors.DictCursor  # Return dict instead of tuple
)

# Helper: Get user ID from cookie
def get_user_id_from_cookie():
    return request.cookies.get("user_id")

# Helper: Set secure cookies in production
def set_secure_cookie(response, user_id):
    response.set_cookie("user_id", str(user_id), max_age=3600, httponly=True, secure=True, samesite='Strict')
    return response

# ROUTES
@app.route("/")
def homepage():
    return render_template("landing.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html")

    try:
        firstName = request.form.get("firstName")
        lastName = request.form.get("lastName")
        dob = request.form.get("dob")
        email = request.form.get("email")
        phone = request.form.get("phone")
        nid = request.form.get("nid")
        password = request.form.get("password")

        # Validate phone number format (11 digits, starts with '01')
        if len(phone) != 11 or not phone.startswith("01"):
            return render_template("signup.html", error="Enter a valid 11-digit phone number starting with '01'.")

        try:
            dob_date = datetime.strptime(dob, "%Y-%m-%d").date()
        except ValueError:
            return render_template("signup.html", error="Invalid DOB format. Use YYYY-MM-DD.")

        # Check if the phone number already exists in the database
        with db.cursor() as cursor:
            cursor.execute("SELECT * FROM user_profile WHERE phone_number = %s", (phone,))
            existing_user = cursor.fetchone()

            if existing_user:
                return render_template("signup.html", phone_error="Phone number already in use")

        # If phone number is available, hash the password and insert the user
        hashed_password = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())

        with db.cursor() as cursor:
            cursor.execute("""INSERT INTO user_profile (first_name, last_name, dob, email, phone_number, nid, password, balance, points, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 1000, 0, 'active')""", 
                (firstName, lastName, dob_date, email, phone, nid, hashed_password.decode()))
            db.commit()

        # Redirect to login page after successful signup
        return redirect("/login")

    except Exception as e:
        return render_template("signup.html", error=f"Signup error: {str(e)}")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")

    phone = request.form.get("phone")
    password = request.form.get("password")

    if not phone or len(phone) != 11 or not phone.startswith("01"):
        return render_template("login.html", error="Enter a valid 11-digit phone number starting with '01'.")

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM user_profile WHERE phone_number = %s", (phone,))
        user = cursor.fetchone()

    if user and bcrypt.checkpw(password.encode("utf-8"), user['password'].encode("utf-8")):
        resp = make_response(redirect("/home"))
        resp = set_secure_cookie(resp, user["user_id"])
        return resp
    else:
        return render_template("login.html", error="Invalid phone number or password.")

@app.route("/home")
def home():
    user_id = get_user_id_from_cookie()
    if not user_id:
        return redirect("/login")

    with db.cursor() as cursor:
        cursor.execute("SELECT * FROM user_profile WHERE user_id = %s", (user_id,))
        user = cursor.fetchone()

    if user:
        return render_template("home.html", user=user)
    else:
        return "User not found", 404

@app.route("/logout")
def logout():
    resp = make_response(redirect("/login"))
    resp.delete_cookie("user_id")
    return resp

# Generate unique transaction ID
def generate_unique_trx_id(cursor):
    while True:
        trx_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cursor.execute("SELECT trx_id FROM send_money WHERE trx_id = %s", (trx_id,))
        if not cursor.fetchone():
            return trx_id


@app.route("/send_now", methods=["GET", "POST"])
def send_now():
    user_id = get_user_id_from_cookie()
    if not user_id:
        return render_template("login.html")

    if request.method == "GET":
        return render_template("send_now.html")

    recipient_phone = request.form.get("recipient_phone")
    recipient_name = request.form.get("recipient_name")
    amount_str = request.form.get("amount")
    save_info = request.form.get("save_info")  # checkbox (value is 'on' if checked)

    try:
        amount = float(amount_str)
        if amount <= 0:
            return render_template("send_now.html", error="Amount must be greater than 0")
    except (ValueError, TypeError):
        return render_template("send_now.html", error="Invalid amount format")

    with db.cursor() as cursor:
        # Check recipient existence
        cursor.execute("SELECT * FROM user_profile WHERE phone_number = %s", (recipient_phone,))
        recipient = cursor.fetchone()

        if not recipient:
            return render_template("send_now.html", error="Phone number not registered.")

        # Check sender balance
        cursor.execute("SELECT balance FROM user_profile WHERE user_id = %s", (user_id,))
        sender = cursor.fetchone()
        if not sender or sender['balance'] < amount:
            return render_template("send_now.html", error="Insufficient balance.")

        # Create transaction
        trx_id = generate_unique_trx_id(cursor)

        cursor.execute("""INSERT INTO send_money (user_id, phone_no, name, amount, trx_id)
                          VALUES (%s, %s, %s, %s, %s)""", (user_id, recipient_phone, recipient_name, amount, trx_id))

        # Update balances
        cursor.execute("UPDATE user_profile SET balance = balance - %s WHERE user_id = %s", (amount, user_id))
        cursor.execute("UPDATE user_profile SET balance = balance + %s WHERE phone_number = %s", (amount, recipient_phone))

        # Save recipient details (if checkbox was ticked)
        if save_info == "on":
            try:
                cursor.execute("""
                    INSERT IGNORE INTO saved_details (user_id, name, phone)
                    VALUES (%s, %s, %s)
                """, (user_id, recipient_name, recipient_phone))
            except Exception as e:
                print("Error saving recipient details:", e)

        # Notifications
        cursor.execute("INSERT INTO notifications (user_id, alerts) VALUES (%s, %s)", 
                       (user_id, f"Sent {amount} to {recipient_name or recipient_phone}"))
        cursor.execute("INSERT INTO notifications (user_id, alerts) VALUES (%s, %s)", 
                       (recipient['user_id'], f"Received {amount} from User {user_id}"))

        db.commit()

    return render_template("home.html", success="Transaction successful!")

@app.route("/schedule_transactions", methods=["GET", "POST"])
def schedule_transactions():
    user_id = get_user_id_from_cookie()
    if not user_id:
        return render_template("login.html")
    if request.method == "GET":
        return render_template("schedule_transactions.html")
    phone = request.form.get("account")
    amount = request.form.get("amount")
    datetime = request.form.get("datetime")
    with db.cursor() as cursor:
        cursor.execute("SELECT balance FROM user_profile WHERE user_id = %s", (user_id,))
        balance = cursor.fetchone()
        if amount> balance:
            return render_template("schedule_transactions.html", error="Amount must be greater than 0")
        else:
            pass
@app.route("/bank", methods=["GET", "POST"])
def bank():
    user_id = get_user_id_from_cookie()
    if not user_id:
        return render_template("login.html")
    
    if request.method == "GET":
        return render_template("bank.html")
    
    account_no = request.form.get("accountNo")
    amount = request.form.get("amount")

    # Validation: Check if input is missing or invalid
    if not account_no or not amount:
        return render_template("bank.html", error="Please fill in all fields.")
    
    try:
        amount = float(amount)
        if amount <= 0:
            raise ValueError("Amount must be positive")
    except ValueError:
        return render_template("bank.html", error="Invalid amount entered.")

    # Start a database transaction
    try:
        with db.cursor() as cursor:
            # Generate a unique transaction ID
            trx_id = generate_unique_trx_id(cursor)

            # Insert into add_money_bank table
            cursor.execute(""" 
                INSERT INTO add_money_bank (user_id, acc_no, amount, trx_id)
                VALUES (%s, %s, %s, %s)
            """, (user_id, account_no, amount, trx_id))

            # Update the user balance
            cursor.execute("""
                UPDATE user_profile SET balance = balance + %s WHERE user_id = %s
            """, (amount, user_id))

            # Insert notification
            cursor.execute("""
                INSERT INTO notifications (user_id, alerts)
                VALUES (%s, %s)
            """, (user_id, f"Add money from Bank account {account_no} for Taka {amount:.2f} successful, Trx ID: {trx_id}"))

            db.commit()

        return render_template("bank.html", success=True)  # Pass success flag to template

    except Exception as e:
        db.rollback()
        return render_template("bank.html", error="Something went wrong. Please try again.")






# Other routes like 'add_money', 'pay_bills', 'investments', etc.
@app.route("/add_money")
def add_money():
    return render_template("add_money.html")

@app.route("/send_money")
def send_money():
    return render_template("send_money.html")

@app.route("/pay_bills")
def pay_bills():
    return render_template("pay_bills.html")

@app.route("/investments")
def investments():
    return render_template("investments.html")

@app.route("/donations")
def donations():
    return render_template("donations.html")

@app.route("/gift_card")
def gift_card():
    return render_template("gift_card.html")

@app.route("/loan")
def loan():
    return render_template("loan.html")

@app.route("/request_money")
def request_money():
    return render_template("request_money.html")

@app.route("/profile")
def profile():
    return render_template("profile.html")

@app.route("/favourite_accounts")
def favourite_accounts():
    return render_template("favourite_accounts.html")

@app.route("/notifications")
def notifications():
    return render_template("notifications.html")

@app.route("/faq")
def faq():
    return render_template("faq.html")

if __name__ == "__main__":
    app.run(debug=True, port=8000)
