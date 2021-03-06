# -*- coding: utf-8 -*-

from django import forms
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.template.defaultfilters import filesizeformat
from django.utils.translation import ugettext_lazy as _


from .models.emailuser import EmailUser


class EmailUserCreationForm(forms.ModelForm):
    """ A form that creates a user, with no privileges,
        from the given email and password. """

    error_messages = {
        'duplicate_email': _("A user with that email already exists."),
        'password_mismatch': _("The two password fields didn't match."),
    }

    email = forms.EmailField()
    password1 = forms.CharField(label=_("Password"),
                                widget=forms.PasswordInput)
    password2 = forms.CharField(label=_("Password confirmation"),
                                widget=forms.PasswordInput,
                                help_text=_("Enter the same password as "
                                            "above, for verification."))

    class Meta:
        model = EmailUser
        fields = ("email", )

    def clean_email(self):
        # Since User.username is unique, this check is redundant,
        # but it sets a nicer error message than the ORM. See #13147.
        email = self.cleaned_data["email"]

        try:
            EmailUser._default_manager.get(email=email)

        except EmailUser.DoesNotExist:
            return email

        raise forms.ValidationError(self.error_messages['duplicate_email'])

    def clean_password2(self):
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise forms.ValidationError(
                self.error_messages['password_mismatch'])
        return password2

    def save(self, commit=True):
        user = super(EmailUserCreationForm, self).save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class EmailUserChangeForm(forms.ModelForm):
    email = forms.EmailField()
    password = ReadOnlyPasswordHashField(
        label=_("Password"),
        help_text=_("Raw passwords are not stored, so "
                    "there is no way to see this user's "
                    "password, but you can change the "
                    "password using "
                    "<a href=\"password/\">this "
                    "form</a>."))

    class Meta:
        model = EmailUser

    def __init__(self, *args, **kwargs):
        super(EmailUserChangeForm, self).__init__(*args, **kwargs)
        f = self.fields.get('user_permissions', None)
        if f is not None:
            f.queryset = f.queryset.select_related('content_type')

    def clean_password(self):
        # Regardless of what the user provides, return the initial value.
        # This is done here, rather than on the field, because the
        # field does not have access to the initial value
        return self.initial["password"]


class RestrictedFileField(forms.FileField):
    """
    Same as FileField, but you can specify:
        * content_types - list containing allowed content_types.
            Example: ['application/pdf', 'image/jpeg']
        * max_upload_size - a number indicating the maximum file size
            allowed for upload.
            2.5MB - 2621440
            5MB - 5242880
            10MB - 10485760
            20MB - 20971520
            50MB - 5242880
            100MB 104857600
            250MB - 214958080
            500MB - 429916160
    """
    def __init__(self, *args, **kwargs):
        self.content_types   = kwargs.pop('content_types', None)
        self.max_upload_size = kwargs.pop('max_upload_size', None)

        super(RestrictedFileField, self).__init__(*args, **kwargs)

    def clean(self, *args, **kwargs):
        data = super(RestrictedFileField, self).clean(*args, **kwargs)

        file = data.file
        try:
            content_type = file.content_type
            if self.content_types and content_type in self.content_types:
                if self.max_upload_size and file._size > self.max_upload_size:
                    raise forms.ValidationError(_(u'File too big: currently {0}'
                                                u', must be smaller than '
                                                u'{1}.').format(
                                                filesizeformat(file._size),
                                                filesizeformat(
                                                    self.max_upload_size)))
            else:
                raise forms.ValidationError(_(u'Filetype not supported. Valid '
                                            u'ones are: %s') %
                                            self.content_types)
        except AttributeError:
            pass

        return data
