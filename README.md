# TestIngenium - Professional Test Management System

TestIngenium is a comprehensive web-based test management system built with Python Flask and SQLite. It enables administrators to create and manage tests while providing users with a seamless testing experience on desktop computers connected to the same office network.

## Features

### Admin Features
- **Dashboard**: View test statistics including completed tests and active sessions
- **User Management**: Add and manage eligible test users
- **Test Configuration**: Set test name, number of questions, time duration, and common password
- **Question Management**: Add multiple-choice questions with 4 options each
- **Results Tracking**: Monitor user performance and pass/fail status

### User Features
- **Secure Login**: Username-based authentication with common password
- **Interactive Test Interface**: Clean, modern interface with 10 questions per page
- **Timer**: Countdown timer with visual warnings
- **Navigation**: Next/Previous buttons and page jumping
- **Save Progress**: Automatic saving of answers
- **Mark for Review**: Flag questions for later review
- **Result Display**: Comprehensive score breakdown and pass/fail status

### Technical Features
- **SQLite Database**: Robust data storage with proper relationships
- **Responsive Design**: Modern Bootstrap-based UI
- **Real-time Features**: Live timer and automatic answer saving
- **Data Integrity**: Proper session management and data validation
- **Network Ready**: Accessible across office network

## Installation and Setup

### Prerequisites
- Python 3.7 or higher
- Modern web browser
- Network connectivity (for office deployment)

### Step 1: Clone or Download
Download all files to your desired directory.

### Step 2: Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 3: Run the Application
```bash
python app.py
```

The application will start on `http://localhost:5000` by default.

### Step 4: Network Access
To make it accessible across your office network:
- Find your computer's IP address
- Access via `http://YOUR_IP_ADDRESS:5067`
- Ensure firewall allows port 5067

## Default Credentials

### Admin Access
- **URL**: `http://localhost:5000/admin`
- **Username**: `admin`
- **Password**: `password`

### Demo User
- **Username**: `demo`
- **Password**: `demo123`
- **Test Password**: `demo` (for Sample Test)

### User Management
- Each user has their own individual password (set by admin)
- Each test has its own access password for security
- Users must enter both their login password and test password

## Quick Start Guide

### For Administrators

1. **Login as Admin**
   - Go to `/admin` and login with default credentials
   - Change admin password in production

2. **Configure Test Settings**
   - Navigate to "User Settings"
   - Set test name, number of questions, duration, and user password
   - Save settings

3. **Add Test Questions**
   - Go to "Test Settings"
   - Add questions with 4 multiple-choice options
   - Mark the correct answer for each question
   - Add enough questions to match your test configuration

4. **Add Users**
   - In "User Settings", add usernames for eligible test takers
   - Share the username and common password with users

5. **Monitor Tests**
   - Use the Dashboard to track active sessions and results
   - View detailed test results and statistics

### For Test Users

1. **Login**
   - Go to the main page (redirects to user login)
   - Enter your username and the common password

2. **Read Instructions**
   - Review test details (number of questions, time limit)
   - Understand navigation and features

3. **Take the Test**
   - Click "Start Test" to begin (timer starts immediately)
   - Answer questions by clicking on options
   - Use "Save" to save progress
   - Use "Mark" to flag questions for review
   - Navigate with Previous/Next buttons
   - Submit when finished

4. **View Results**
   - See your score and pass/fail status
   - Review performance breakdown
   - Logout when done

## Database Schema

The system uses SQLite with the following tables:

### TestSettings
- Test configuration (name, questions, duration, password)

### Users  
- User management (usernames, eligibility status)

### UserAnswers
- Question bank (questions, options, correct answers)

### UsersTestResults
- Test results (scores, pass/fail, timestamps)

### UserTestSessions
- Active test tracking (start times, current page)

### UserResponses
- User answer storage (selected answers, marked questions)

## Configuration

### Test Settings
- **Test Name**: Customizable test title
- **Number of Questions**: 1-100 questions
- **Time Duration**: 1-300 minutes
- **Common Password**: Shared password for all users
- **Questions per Page**: Fixed at 10 for optimal UX

### Passing Grade
- Default: 70% (configurable in app.py)
- Can be modified in the `submit_test()` function

## Security Features

- Session-based authentication
- Admin/user role separation
- Automatic logout on session expiry
- Prevention of accidental page refresh during tests
- Secure password handling

## Troubleshooting

### Common Issues

1. **Database Errors**
   - Delete `testingenium.db` to reset database
   - Restart application to recreate tables

2. **Timer Issues**
   - Ensure JavaScript is enabled
   - Check browser compatibility

3. **Network Access Problems**
   - Verify firewall settings
   - Check IP address configuration
   - Ensure port 5067 is available

4. **Questions Not Displaying**
   - Verify questions are added via admin panel
   - Check test settings configuration

### Performance Tips

- Limit concurrent users based on server capacity
- Regular database cleanup for large deployments
- Monitor system resources during peak usage

## Development

### Customization
- Modify templates in `/templates` for UI changes
- Update styles in `base.html` for design changes
- Adjust business logic in `app.py`

### Adding Features
- Database schema can be extended
- Additional question types can be implemented
- Reporting features can be added

## Support

For technical support or feature requests:
1. Check this README for common solutions
2. Review the code comments for implementation details
3. Test in a development environment before production deployment

## License

This software is provided as-is for educational and professional use. Modify as needed for your specific requirements.

---

**TestIngenium** - Professional Test Management Made Simple 