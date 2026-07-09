from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, NotificationPreference


class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ['username', 'patient_id', 'email', 'role', 'gender', 'phone', 'is_staff']
    list_filter = ['role', 'gender']
    search_fields = ['username', 'patient_id', 'email', 'first_name', 'last_name', 'phone']
    fieldsets = UserAdmin.fieldsets + (
        (None, {'fields': ('role', 'patient_id', 'phone', 'gender', 'date_of_birth', 'address', 'specialization', 'consultation_fee')}),
    )
    add_fieldsets = UserAdmin.add_fieldsets + (
        (None, {'fields': ('role', 'phone', 'gender', 'date_of_birth', 'address', 'specialization', 'consultation_fee')}),
    )
    readonly_fields = ['patient_id']


admin.site.register(User, CustomUserAdmin)

@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(admin.ModelAdmin):
    list_display = ['user', 'email_enabled', 'sms_enabled', 'appointment_reminders', 'lab_results', 'updated_at']
    list_filter = ['email_enabled', 'sms_enabled', 'appointment_reminders', 'lab_results']
    search_fields = ['user__username', 'user__first_name']
