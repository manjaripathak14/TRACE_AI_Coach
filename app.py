
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "trace-project-secret-key"


# Home / Login page
@app.route("/")
def home():
    return render_template("index.html")


# Login verification
@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")

    # Temporary testing credentials
    if username == "manjari" and password == "trace123":
        session["username"] = username
        return redirect(url_for("dashboard"))

    return "Invalid username or password. Please go back and try again.", 401


# Dashboard page
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect(url_for("home"))

    return render_template("dashboard.html")


# Logout
@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(debug=True)