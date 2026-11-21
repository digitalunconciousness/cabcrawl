"""Authentication and profile forms."""

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed
from wtforms import (
    BooleanField,
    FileField,
    PasswordField,
    RadioField,
    StringField,
    SubmitField,
)
from wtforms.validators import DataRequired, EqualTo, Length


class LoginForm(FlaskForm):
    username = StringField(
        "Username", validators=[DataRequired(), Length(min=2, max=80)]
    )
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember Me")
    submit = SubmitField("Login")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    new_password = PasswordField(
        "New password",
        validators=[DataRequired(), Length(min=8, message="Use at least 8 characters.")])
    confirm_password = PasswordField(
        "Type it again",
        validators=[DataRequired(),
                    EqualTo("new_password", message="The two passwords are different.")])


class ProfileForm(FlaskForm):
    profile_picture = FileField(
        "Photo",
        validators=[FileAllowed(["jpg", "jpeg", "png", "gif"],
                                "That isn't a photo. Use PNG, JPG or GIF.")],
    )


class ThemeForm(FlaskForm):
    """The desktop look (Profile). Phones always use synthwave."""
    desk_theme = RadioField("Desktop look", validators=[DataRequired()], choices=[])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.models.user import DESK_THEMES
        self.desk_theme.choices = list(DESK_THEMES.items())
