import joblib
import pandas as pd

# Load just the model
model = joblib.load("xgb_model.pkl")
input_columns = model.get_booster().feature_names  # Assumes model was trained with X containing proper column names

def predict_salary(profile_dict):
    df = pd.DataFrame([profile_dict])
    df = pd.get_dummies(df)

    # Add missing columns if any
    for col in input_columns:
        if col not in df.columns:
            df[col] = 0

    df = df[input_columns]  # Reorder to match training
    prediction = model.predict(df)[0]
    return round(float(prediction), 2)
