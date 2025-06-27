from flask import Flask, render_template, request, redirect, url_for, session, abort
from flask_sqlalchemy import SQLAlchemy
from flask_session import Session
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView
from flask_admin.form import ImageUploadField
from wtforms import SelectMultipleField
from wtforms.widgets import CheckboxInput
from markupsafe import Markup
import os
import requests
import google.oauth2.credentials
import google_auth_oauthlib.flow
import googleapiclient.discovery
from sqlalchemy.dialects.postgresql import ARRAY, INTEGER
from datetime import datetime, timedelta
from model import predict_salary

# --- App & Config ---
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DATABASE_URL")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "super-secret-key"
app.config["SESSION_TYPE"] = "filesystem"


uri = os.environ.get("DATABASE_URL")
if uri and uri.startswith("postgres://"):
    uri = uri.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = uri

DAY_CHOICES = {
    0: "Monday",
    1: "Tuesday",
    2: "Wednesday",
    3: "Thursday",
    4: "Friday",
    5: "Saturday",
    6: "Sunday"
}

TIME_CHOICES = {
    0: "09:00",
    1: "10:00",
    2: "11:00",
    3: "14:00",
    4: "15:00",
    5: "16:00"
}

global input_columns

# --- Init Extensions ---
Session(app)
db = SQLAlchemy(app)
admin = Admin(app, name='Mentor Admin', template_mode='bootstrap4')

with app.app_context():
    try:
        db.create_all()
        print("✅ Tables created (or already exist)")
    except Exception as e:
        print("⚠️ Failed to create tables:", e)

# --- Models ---
class Mentor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    bio_line1 = db.Column(db.String(100))
    bio_line2 = db.Column(db.String(100))
    bio_line3 = db.Column(db.String(100))
    photo = db.Column(db.String(200))
    available_days = db.Column(ARRAY(INTEGER), default=[])
    available_times = db.Column(ARRAY(INTEGER), default=[])


class Appointment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_name = db.Column(db.String(100))
    email = db.Column(db.String(120))
    mentor_id = db.Column(db.Integer, db.ForeignKey('mentor.id', ondelete='CASCADE'))  # 👈 add this
    date = db.Column(db.String(20))
    time = db.Column(db.String(20))
    meeting_link = db.Column(db.String(200))
    mentor = db.relationship("Mentor", backref=db.backref("appointments", cascade="all, delete-orphan"))


# --- Custom Fields ---
class HorizontalCheckboxWidget:
    def __call__(self, field, **kwargs):
        html = '<div style="display: flex; flex-wrap: wrap; gap: 10px;">'
        for subfield in field:
            html += f'<label style="margin-right: 10px;">{subfield()} {subfield.label.text}</label>'
        html += '</div>'
        return Markup(html)

class MultiCheckboxField(SelectMultipleField):
    widget = HorizontalCheckboxWidget()
    option_widget = CheckboxInput()

    def process_formdata(self, valuelist):
        self.data = [int(x) for x in valuelist if x.isdigit()]

# --- Admin View ---
class MentorAdminView(ModelView):
    form_overrides = {
        'available_days': MultiCheckboxField,
        'available_times': MultiCheckboxField
    }

    form_args = {
        'available_days': {
            'coerce': int,
            'choices': [(k, v) for k, v in DAY_CHOICES.items()]
        },
        'available_times': {
            'coerce': int,
            'choices': [(k, v) for k, v in TIME_CHOICES.items()]
        }
    }

    form_extra_fields = {
        'photo': ImageUploadField(
            'Photo',
            base_path=os.path.join(os.path.dirname(__file__), 'static/images'),
            relative_path='static/images/',
            url_relative_path='/static/images/',
            allow_overwrite=True
        )
    }

    column_formatters = {
        'available_days': lambda v, c, m, p: ', '.join(DAY_CHOICES.get(int(d), str(d)) for d in m.available_days if str(d).isdigit()),
        'available_times': lambda v, c, m, p: ', '.join(TIME_CHOICES.get(int(t), str(t)) for t in m.available_times if str(t).isdigit())
    }
    form_columns = ['name', 'bio_line1', 'bio_line2', 'bio_line3', 'available_days', 'available_times', 'photo']

    def on_model_change(self, form, model, is_created):
        model.available_days = [int(v) for v in form.available_days.data if str(v).isdigit()]
        model.available_times = [int(v) for v in form.available_times.data if str(v).isdigit()]

# --- Helpers ---
def get_available_slots(mentor, date):
    weekday_index = date.weekday()
    if weekday_index not in mentor.available_days:
        return []

    booked = {
        a.time for a in Appointment.query.filter_by(mentor_id=mentor.id, date=date.isoformat()).all()
    }
    return [TIME_CHOICES[i] for i in mentor.available_times if TIME_CHOICES[i] not in booked]

# --- Register admin models ---
admin.add_view(MentorAdminView(Mentor, db.session))
admin.add_view(ModelView(Appointment, db.session))


@app.route("/predict", methods=["POST"])
def predict_api():
    data = request.get_json()
    try:
        salary = predict_salary(data)
        return {"predicted_salary": salary}
    except Exception as e:
        return {"error": str(e)}, 500



@app.route('/test-db')
def test_db():
    try:
        db.session.execute("SELECT * FROM mentor LIMIT 1")
        return "✅ Mentor table exists."
    except Exception as e:
        return f"❌ DB error: {e}"



# --- Routes ---
@app.route('/', methods=['GET', 'POST'])
def form_page():
    result = None
    if request.method == 'POST':
        form_data = {
            "experience_years": float(request.form["experience_years"]),
            "num_skills": int(request.form["num_skills"]),
            "num_certificates": int(request.form["num_certificates"]),
            "category": request.form["category"],
            "profession_category": request.form["profession_category"]
        }
        try:
            salary = predict_salary(form_data)
            result = {"salary": f"{salary:,.0f} KZT"}
        except Exception as e:
            result = {"error": f"Prediction error: {e}"}
    return render_template('form.html', result=result)


@app.route('/mentors')
def mentor_list():
    mentors = Mentor.query.all()
    return render_template("mentors.html", mentors=mentors)


@app.route('/appointment', methods=['GET', 'POST'])
def book_appointment():
    mentors = Mentor.query.all()
    mentor_data = {
        str(m.id): {
            "available_days": m.available_days,
            "available_times": m.available_times
        }
        for m in mentors
    }
    selected_mentor_id = request.form.get("mentor_id")
    selected_mentor = db.session.get(Mentor, int(selected_mentor_id)) if selected_mentor_id else None

    selected_time = request.form.get("time") if request.method == 'POST' else None
    selected_date = request.form.get("date") if request.method == 'POST' else None
    meeting_link = None

    if request.method == 'POST' and selected_mentor:
        final_time_str = selected_time
        date = selected_date.split("T")[0] if selected_date else datetime.today().strftime("%Y-%m-%d")

        if "credentials" not in session:
            session["pending_booking"] = {
                "mentor_id": selected_mentor.id,
                "date": date,
                "time": final_time_str
            }
            return redirect(url_for("oauth2callback"))

        # Authenticated — generate Meet link
        start_dt = datetime.strptime(f"{date} {final_time_str}", "%Y-%m-%d %H:%M")
        end_dt = start_dt + timedelta(minutes=30)

        meeting_link = create_google_meet_link(
            summary=f"Mentoring with {selected_mentor.name}",
            start_time=start_dt.isoformat(),
            end_time=end_dt.isoformat()
        )

        appointment = Appointment(
            user_name="Unknown",
            email="user@example.com",
            mentor_id=selected_mentor.id,
            date=date,
            time=final_time_str,
            meeting_link=meeting_link
        )
        db.session.add(appointment)
        db.session.commit()

    return render_template('appointment.html',
                           mentors=mentors,
                           mentor_data=mentor_data,
                           meeting_link=meeting_link,
                           DAY_CHOICES=DAY_CHOICES,
                           TIME_CHOICES=TIME_CHOICES)


@app.route("/oauth2callback")
def oauth2callback():
    flow = google_auth_oauthlib.flow.Flow.from_client_secrets_file(
        "/etc/secrets/client_secret.json",
        scopes=["https://www.googleapis.com/auth/calendar.events"],
        redirect_uri=url_for("oauth2callback", _external=True)
    )

    if "code" not in request.args:
        auth_url, _ = flow.authorization_url(prompt='consent')
        return redirect(auth_url)
    else:
        flow.fetch_token(code=request.args.get("code"))
        credentials = flow.credentials
        session["credentials"] = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes
        }

        if "pending_booking" in session:
            booking = session.pop("pending_booking")
            return redirect(url_for("book_appointment", mentor_id=booking["mentor_id"]))

        return redirect(url_for("mentor_list"))


def create_google_meet_link(summary="Mentor Meeting", start_time=None, end_time=None):
    if "credentials" not in session:
        return None

    creds = google.oauth2.credentials.Credentials(**session["credentials"])
    service = googleapiclient.discovery.build("calendar", "v3", credentials=creds)

    event = {
        "summary": summary,
        "start": {"dateTime": start_time, "timeZone": "Asia/Almaty"},
        "end": {"dateTime": end_time, "timeZone": "Asia/Almaty"},
        "conferenceData": {
            "createRequest": {
                "requestId": f"req-{datetime.now().timestamp()}",
                "conferenceSolutionKey": {"type": "hangoutsMeet"}
            }
        }
    }

    created_event = service.events().insert(
        calendarId="primary", body=event, conferenceDataVersion=1
    ).execute()

    print("✅ Google Meet created:", created_event.get("hangoutLink"))
    return created_event["hangoutLink"]




