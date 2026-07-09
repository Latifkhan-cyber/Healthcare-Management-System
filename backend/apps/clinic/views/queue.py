# Queue Management Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from datetime import date

from ..models import QueueToken, Appointment
from backend.apps.accounts.models import User
from .appointments import estimate_wait_time


@login_required
def queue_display(request):
    """Public-facing queue display for waiting room TV. No login required."""
    today = date.today()

    doctors = User.objects.filter(role='DOCTOR').order_by('first_name')
    doctors_queue = []
    now_serving = None

    for doctor in doctors:
        active_tokens = QueueToken.objects.filter(
            appointment__doctor=doctor,
            appointment__appointment_date=today,
            is_active=True
        ).select_related('appointment__patient').order_by('token_number')

        tokens_list = list(active_tokens)

        if tokens_list:
            current = tokens_list[0]
            if now_serving is None:
                now_serving = current

            waiting = tokens_list[1:] if len(tokens_list) > 1 else []

            queue_data = []
            for t in tokens_list:
                patient_name = t.appointment.patient.get_full_name() or t.appointment.patient.username
                initials = ''.join([n[0].upper() for n in patient_name.split()[:2]])
                queue_data.append({
                    'token_number': t.token_number,
                    'patient_initials': initials,
                })

            doctors_queue.append({
                'doctor': doctor,
                'active_token': current,
                'waiting_count': len(waiting),
                'queue': queue_data,
            })

    return render(request, 'clinic/queue_display.html', {
        'doctors_queue': doctors_queue,
        'now_serving': now_serving,
    })


@login_required
def manage_queue(request):
    if request.user.role == 'PATIENT':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    if request.user.role == 'DOCTOR':
        tokens = QueueToken.objects.filter(
            appointment__doctor=request.user,
            is_active=True
        ).select_related('appointment__patient').order_by('token_number')
    else:
        tokens = QueueToken.objects.filter(
            is_active=True
        ).select_related('appointment__patient', 'appointment__doctor').order_by('token_number')

    active_token = tokens.first()
    return render(request, 'clinic/manage_queue.html', {
        'tokens': tokens,
        'active_token': active_token,
    })


@login_required
def emergency_priority(request, token_id):
    """Mark a token as emergency — move to front of doctor's queue."""
    if request.user.role not in ('ADMIN', 'RECEPTIONIST', 'DOCTOR'):
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    token = get_object_or_404(QueueToken, id=token_id)

    # Doctors can only manage their own queue
    if request.user.role == 'DOCTOR' and token.appointment.doctor != request.user:
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    if not token.is_active:
        messages.error(request, "This token is no longer active.")
        return redirect('clinic:manage_queue')

    today = date.today()
    doctor = token.appointment.doctor

    # Get the minimum active token number for this doctor today
    min_token = QueueToken.objects.filter(
        appointment__doctor=doctor,
        appointment__appointment_date=today,
        is_active=True
    ).order_by('token_number').first()

    if min_token and token.id != min_token.id:
        # Move this token to the front by swapping numbers
        old_number = token.token_number
        new_number = min_token.token_number

        # Use a temporary number to avoid unique constraint issues
        temp_number = 999999
        token.token_number = temp_number
        token.save()

        min_token.token_number = old_number
        min_token.save()

        token.token_number = new_number
        token.save()

        messages.success(
            request,
            f"Token #{new_number} ({token.appointment.patient.get_full_name()}) moved to front of queue."
        )
    else:
        messages.info(request, "This token is already at the front of the queue.")

    return redirect('clinic:manage_queue')


@login_required
def call_next(request):
    """Mark current token as done and activate next."""
    if request.user.role != 'DOCTOR':
        return redirect('dashboard')
    current = QueueToken.objects.filter(
        appointment__doctor=request.user,
        is_active=True
    ).order_by('token_number').first()
    if current:
        current.is_active = False
        current.appointment.status = 'COMPLETED'
        current.appointment.save()
        current.save()
        # Notify next patient in queue
        next_token = QueueToken.objects.filter(
            appointment__doctor=request.user,
            is_active=True
        ).order_by('token_number').first()

        if next_token:
            from .notifications import create_notification
            create_notification(
                recipient=next_token.appointment.patient,
                title="Your Turn Next",
                message=f"Token #{next_token.token_number}, you are next in Dr. {request.user.get_full_name()}'s queue. Please proceed to the consultation room.",
                notification_type='QUEUE_YOUR_TURN',
            )

        messages.success(request, f"Token #{current.token_number} completed.")
    return redirect('clinic:manage_queue')


@login_required
def serve_patient(request, token_id):
    """Doctor marks patient as seen — admin will record details later."""
    if request.user.role != 'DOCTOR':
        return redirect('dashboard')
    token = get_object_or_404(
        QueueToken, id=token_id,
        appointment__doctor=request.user
    )
    appointment = token.appointment

    if request.method == 'POST':
        # Doctor just marks as seen — no medical data entry
        appointment.status = 'COMPLETED'
        appointment.save()
        token.is_active = False
        token.save()

        messages.success(
            request,
            f"Patient {appointment.patient.get_full_name()} marked as seen. Admin will record consultation details."
        )
        return redirect('clinic:manage_queue')

    # Show patient info for doctor to review before marking
    return render(request, 'clinic/serve_patient.html', {
        'appointment': appointment,
        'token': token,
    })