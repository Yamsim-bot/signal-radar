"""Run the Flask web app, pre-loading stdlib calendar to avoid shadowing."""
import calendar  # must be before importing signal_radar
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_radar.web import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
