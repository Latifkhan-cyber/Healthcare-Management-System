# Patient Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Sum
from datetime import date

from ..models import (
    Appointment, QueueToken, PatientHistory,
    Payment, DoctorSchedule, Notification, DoctorReview, LabTest
)
from ..forms import PatientHistoryForm
from backend.apps.accounts.models import User

from .appointments import estimate_wait_time
from ..notification_service import create_notification


@login_required
def patient_profile(request):
    user = request.user
    total_appointments = Appointment.objects.filter(patient=user).count()
    completed_appointments = Appointment.objects.filter(
        patient=user, status='COMPLETED'
    ).count()
    total_spent = Payment.objects.filter(
        appointment__patient=user, status='PAID'
    ).aggregate(total=Sum('amount'))['total'] or 0

    recent_history = PatientHistory.objects.filter(
        patient=user
    ).select_related('doctor')[:5]

    if request.method == 'POST':
        user.first_name = request.POST.get('first_name', user.first_name)
        user.last_name = request.POST.get('last_name', user.last_name)
        user.phone = request.POST.get('phone', user.phone)
        user.email = request.POST.get('email', user.email)
        user.gender = request.POST.get('gender') or None
        user.date_of_birth = request.POST.get('date_of_birth') or None
        user.address = request.POST.get('address', user.address)

        # Handle profile photo upload
        if request.FILES.get('profile_photo'):
            # Delete old photo if exists
            if user.profile_photo:
                import os
                from django.conf import settings
                old_path = os.path.join(settings.MEDIA_ROOT, user.profile_photo.name)
                if os.path.isfile(old_path):
                    os.remove(old_path)
            user.profile_photo = request.FILES['profile_photo']

        user.save()
        messages.success(request, "Profile updated successfully!")
        return redirect('clinic:patient_profile')

    return render(request, 'clinic/patient_profile.html', {
        'total_appointments': total_appointments,
        'completed_appointments': completed_appointments,
        'total_spent': total_spent,
        'recent_history': recent_history,
    })


@login_required
def remove_profile_photo(request):
    """Remove the user's profile photo."""
    if request.method == 'POST' or request.method == 'GET':
        user = request.user
        if user.profile_photo:
            import os
            from django.conf import settings
            old_path = os.path.join(settings.MEDIA_ROOT, user.profile_photo.name)
            if os.path.isfile(old_path):
                os.remove(old_path)
            user.profile_photo.delete(save=True)
            messages.success(request, "Profile photo removed.")
        return redirect('clinic:patient_profile')


@login_required
def walkin_booking(request):
    """Admin books a walk-in patient with cash payment — instant confirm."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    doctors = User.objects.filter(role='DOCTOR')
    existing_patients = User.objects.filter(role='PATIENT')

    if request.method == 'POST':
        patient_type = request.POST.get('patient_type', 'existing')

        if patient_type == 'new':
            # Quick register a new patient
            first_name = request.POST.get('first_name', '').strip()
            last_name = request.POST.get('last_name', '').strip()
            phone = request.POST.get('phone', '').strip()
            email = request.POST.get('email', '').strip()

            if not first_name:
                messages.error(request, "Patient name is required.")
                return redirect('clinic:walkin_booking')

            # Generate a unique username
            base_username = (first_name + last_name).lower().replace(' ', '')[:15]
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = base_username + str(counter)
                counter += 1

            patient = User.objects.create_user(
                username=username,
                password='patient123',
                first_name=first_name,
                last_name=last_name,
                email=email or '',
                phone=phone or '',
                gender=request.POST.get('gender') or None,
                date_of_birth=request.POST.get('date_of_birth') or None,
                address=request.POST.get('address', ''),
                role='PATIENT',
            )
        else:
            patient_id = request.POST.get('patient_id')
            patient = get_object_or_404(User, id=patient_id, role='PATIENT')

        doctor_id = request.POST.get('doctor_id')
        doctor = get_object_or_404(User, id=doctor_id, role='DOCTOR')
        appt_date = request.POST.get('appointment_date')
        time_slot = request.POST.get('time_slot')
        reason = request.POST.get('reason', 'Walk-in consultation')

        # Create appointment — CONFIRMED immediately
        appointment = Appointment.objects.create(
            patient=patient,
            doctor=doctor,
            appointment_date=appt_date,
            time_slot=time_slot,
            status='CONFIRMED',
            reason=reason,
        )

        # Create cash payment — PAID immediately
        Payment.objects.create(
            appointment=appointment,
            amount=doctor.consultation_fee,
            method='CASH',
            status='PAID',
        )

        # Generate queue token
        existing_count = QueueToken.objects.filter(
            appointment__doctor=doctor,
            appointment__appointment_date=appt_date,
            is_active=True,
        ).count()
        token_number = existing_count + 1
        estimated_wait = estimate_wait_time(doctor, appt_date)
        QueueToken.objects.create(
            appointment=appointment,
            token_number=token_number,
            estimated_wait_minutes=estimated_wait,
        )

        pid = patient.patient_id or "N/A"
        messages.success(
            request,
            f"Walk-in booking confirmed! Patient ID: {pid} — {patient.get_full_name()} with Dr. {doctor.get_full_name()}. Queue Token #{token_number}."
        )
        return redirect('clinic:manage_queue')

    return render(request, 'clinic/walkin_booking.html', {
        'doctors': doctors,
        'existing_patients': existing_patients,
    })


@login_required
def register_patient(request):
    """Receptionist registers a new patient account."""
    if request.user.role not in ('ADMIN', 'RECEPTIONIST'):
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        phone = request.POST.get('phone', '').strip()
        gender = request.POST.get('gender') or None
        date_of_birth = request.POST.get('date_of_birth') or None
        address = request.POST.get('address', '').strip()
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')

        # Validation
        if not first_name or not last_name:
            messages.error(request, "First name and last name are required.")
            return redirect('clinic:register_patient')
        if not username:
            messages.error(request, "Username is required.")
            return redirect('clinic:register_patient')
        if User.objects.filter(username=username).exists():
            messages.error(request, f"Username '{username}' is already taken.")
            return redirect('clinic:register_patient')
        if not password1 or not password2:
            messages.error(request, "Password is required.")
            return redirect('clinic:register_patient')
        if password1 != password2:
            messages.error(request, "Passwords do not match.")
            return redirect('clinic:register_patient')

        patient = User.objects.create_user(
            username=username,
            password=password1,
            first_name=first_name,
            last_name=last_name,
            email=email or '',
            phone=phone or '',
            gender=gender,
            date_of_birth=date_of_birth,
            address=address,
            role='PATIENT',
        )

        pid = patient.patient_id or "N/A"
        messages.success(
            request,
            f"Patient registered successfully! ID: {pid} — {patient.get_full_name()}. Default login: {username}"
        )
        return redirect('clinic:patient_detail', patient_id=patient.patient_id)

    return render(request, 'clinic/register_patient.html')


@login_required
def patient_lookup(request):
    """Search patient by patient_id, name, or phone."""
    query = request.GET.get('q', '').strip()
    patients = None
    selected_patient = None

    if query:
        patients = User.objects.filter(
            role='PATIENT'
        ).filter(
            Q(patient_id__icontains=query) |
            Q(username__icontains=query) |
            Q(first_name__icontains=query) |
            Q(last_name__icontains=query) |
            Q(phone__icontains=query) |
            Q(email__icontains=query)
        ).distinct()[:20]

        # If exact match by patient_id, show full details
        if len(query) <= 12:
            try:
                selected_patient = User.objects.get(
                    role='PATIENT', patient_id__iexact=query
                )
            except User.DoesNotExist:
                pass

    return render(request, 'clinic/patient_lookup.html', {
        'query': query,
        'patients': patients,
        'selected_patient': selected_patient,
    })


@login_required
def patient_detail(request, patient_id):
    """Full patient profile with all history, visits, lab tests."""
    patient = get_object_or_404(User, patient_id=patient_id, role='PATIENT')

    # Get all data
    appointments = Appointment.objects.filter(
        patient=patient
    ).select_related('doctor', 'queue_token', 'payment').order_by('-appointment_date')[:20]

    history = PatientHistory.objects.filter(
        patient=patient
    ).select_related('doctor').order_by('-visit_date')[:20]

    lab_tests = LabTest.objects.filter(
        patient=patient
    ).select_related('doctor').order_by('-ordered_date')[:20]

    follow_ups = PatientHistory.objects.filter(
        patient=patient, follow_up_required=True
    ).order_by('follow_up_date')[:10]

    # Stats
    stats = {
        'total_visits': Appointment.objects.filter(patient=patient).count(),
        'completed': Appointment.objects.filter(patient=patient, status='COMPLETED').count(),
        'total_spent': Payment.objects.filter(
            appointment__patient=patient, status='PAID'
        ).aggregate(total=Sum('amount'))['total'] or 0,
        'lab_tests': LabTest.objects.filter(patient=patient).count(),
    }

    return render(request, 'clinic/patient_detail.html', {
        'p': patient,
        'appointments': appointments,
        'history': history,
        'lab_tests': lab_tests,
        'follow_ups': follow_ups,
        'stats': stats,
    })