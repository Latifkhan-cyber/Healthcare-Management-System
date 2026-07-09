from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.db import models
from django.utils import timezone
import uuid


def generate_patient_id():
    """Generate a unique patient ID like MQ-000001."""
    # Count existing patients to get next number
    last_patient = User.objects.filter(
        role='PATIENT',
        patient_id__isnull=False
    ).exclude(patient_id='').order_by('-id').first()

    if last_patient and last_patient.patient_id:
        try:
            last_num = int(last_patient.patient_id.split('-')[1])
            next_num = last_num + 1
        except (ValueError, IndexError):
            next_num = User.objects.filter(role='PATIENT').count() + 1
    else:
        next_num = User.objects.filter(role='PATIENT').count() + 1

    return f"MQ-{next_num:06d}"



class NotificationPreference(models.Model):
    """User notification delivery preferences."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='notification_preferences'
    )
    email_enabled = models.BooleanField(default=True, help_text="Receive notifications via email")
    sms_enabled = models.BooleanField(default=True, help_text="Receive notifications via SMS")
    appointment_reminders = models.BooleanField(default=True, help_text="Receive appointment reminders")
    queue_notifications = models.BooleanField(default=True, help_text="Receive queue turn notifications")
    lab_results = models.BooleanField(default=True, help_text="Receive lab result notifications")
    follow_up_reminders = models.BooleanField(default=True, help_text="Receive follow-up reminders")
    payment_notifications = models.BooleanField(default=True, help_text="Receive payment notifications")
    general_notifications = models.BooleanField(default=True, help_text="Receive general notifications")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Notification Preference"
        verbose_name_plural = "Notification Preferences"
        ordering = ['-updated_at']

    def __str__(self):
        return f"{self.user.username} — Notification Preferences"
class User(AbstractUser):
    ROLE_CHOICES = (
        ('ADMIN', 'Admin'),
        ('DOCTOR', 'Doctor'),
        ('RECEPTIONIST', 'Receptionist'),
        ('PATIENT', 'Patient'),
    )
    GENDER_CHOICES = (
        ('MALE', 'Male'),
        ('FEMALE', 'Female'),
        ('OTHER', 'Other'),
    )
    role = models.CharField(max_length=12, choices=ROLE_CHOICES, default='PATIENT')
    patient_id = models.CharField(
        max_length=12, unique=True, blank=True, null=True,
        help_text="Unique patient registration number (auto-generated)"
    )
    phone = models.CharField(max_length=15, blank=True, null=True)
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES, blank=True, null=True)
    date_of_birth = models.DateField(blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    specialization = models.CharField(max_length=200, blank=True, null=True)
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=500.00)
    profile_photo = models.ImageField(
        upload_to='profile_photos/%Y/%m/',
        blank=True, null=True,
        help_text="Profile photo (optional)"
    )

    def __str__(self):
        if self.role == 'PATIENT' and self.patient_id:
            return f"{self.patient_id} — {self.get_full_name()}"
        return f"{self.username} ({self.get_role_display()})"

    def save(self, *args, **kwargs):
        # Auto-generate patient_id for new patients
        if self.role == 'PATIENT' and not self.patient_id:
            self.patient_id = generate_patient_id()
        super().save(*args, **kwargs)

    def get_age(self):
        if self.date_of_birth:
            from datetime import date
            today = date.today()
            return today.year - self.date_of_birth.year - (
                (today.month, today.day) < (self.date_of_birth.month, self.date_of_birth.day)
            )
        return None
