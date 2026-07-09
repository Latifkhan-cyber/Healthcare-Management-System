# Appointments Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q
from datetime import date, timedelta, datetime
from django.http import JsonResponse

from ..models import Appointment, QueueToken, Payment, DoctorSchedule
from ..forms import AppointmentForm
from backend.apps.accounts.models import User


def estimate_wait_time(doctor, appointment_date):
    """
    Estimate wait time based on doctor's queue.
    Uses 15 min per patient as average consultation time.
    Can be enhanced with actual timing data later.
    """
    avg_time = 15  # minutes per patient
    patients_ahead = QueueToken.objects.filter(
        appointment__doctor=doctor,
        appointment__appointment_date=appointment_date,
        is_active=True
    ).count()
    return patients_ahead * avg_time


@login_required
def book_appointment(request):
    if request.method == 'POST':
        form = AppointmentForm(request.POST)
        if form.is_valid():
            appointment = form.save(commit=False)
            appointment.patient = request.user
            appointment.save()
            messages.success(
                request,
                "Appointment booked successfully! Please proceed to payment."
            )
            return redirect('clinic:make_payment', appointment_id=appointment.id)
    else:
        form = AppointmentForm()

    doctors = User.objects.filter(role='DOCTOR').order_by('first_name')
    specializations = list(User.objects.filter(role='DOCTOR')
                          .exclude(specialization__isnull=True)
                          .exclude(specialization='')
                          .values_list('specialization', flat=True)
                          .distinct().order_by('specialization'))

    return render(request, 'clinic/book_appointment.html', {
        'form': form,
        'doctors': doctors,
        'specializations': specializations,
        'today': date.today(),
    })


@login_required
def my_appointments(request):
    appointments = Appointment.objects.filter(
        patient=request.user
    ).select_related('doctor', 'queue_token', 'payment')

    today = date.today()
    stats = {
        'total': appointments.count(),
        'completed': appointments.filter(status='COMPLETED').count(),
        'upcoming': appointments.filter(
            status='CONFIRMED', appointment_date__gte=today
        ).count(),
        'pending': appointments.filter(status='PENDING').count(),
    }

    return render(request, 'clinic/my_appointments.html', {
        'appointments': appointments,
        'stats': stats,
    })


@login_required
def get_doctor_slots(request):
    """AJAX endpoint: returns available 30-min time slots for a doctor on a given date."""
    doctor_id = request.GET.get('doctor_id')
    date_str = request.GET.get('date')

    if not doctor_id or not date_str:
        return JsonResponse({'error': 'Missing doctor_id or date'}, status=400)

    try:
        selected_date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return JsonResponse({'error': 'Invalid date format'}, status=400)

    day_of_week = selected_date.weekday()

    # Get doctor's schedule for this day
    schedules = DoctorSchedule.objects.filter(
        doctor_id=doctor_id,
        day_of_week=day_of_week,
        is_available=True
    )

    if not schedules.exists():
        return JsonResponse({'slots': [], 'message': 'Doctor is not available on this day.'})

    # Generate 30-min slots from schedule
    slots = []
    for sched in schedules:
        current = datetime.combine(selected_date, sched.start_time)
        end = datetime.combine(selected_date, sched.end_time)
        while current < end:
            time_str = current.strftime('%H:%M')
            # Check if slot is already booked
            is_booked = Appointment.objects.filter(
                doctor_id=doctor_id,
                appointment_date=selected_date,
                time_slot=time_str,
                status__in=['PENDING', 'CONFIRMED']
            ).exists()
            slots.append({
                'time': time_str,
                'display': current.strftime('%I:%M %p'),
                'available': not is_booked
            })
            current += timedelta(minutes=30)

    return JsonResponse({'slots': slots})


@login_required
def cancel_appointment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment, id=appointment_id, patient=request.user
    )
    if request.method == 'POST':
        if appointment.status in ('PENDING', 'CONFIRMED'):
            appointment.status = 'CANCELLED'
            appointment.save()
            messages.success(request, "Appointment cancelled.")
        else:
            messages.error(request, "This appointment cannot be cancelled.")
        return redirect('clinic:my_appointments')
    return render(request, 'clinic/cancel_appointment.html', {
        'appointment': appointment
    })


@login_required
def appointment_detail(request, appointment_id):
    """View full details of a specific appointment."""
    appointment = get_object_or_404(
        Appointment, id=appointment_id
    )

    # Role-based access: patient sees own, doctor sees own, admin/receptionist sees all
    if request.user.role == 'PATIENT' and appointment.patient != request.user:
        messages.error(request, "Access denied.")
        return redirect('dashboard')
    if request.user.role == 'DOCTOR' and appointment.doctor != request.user:
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    # Get related data
    token = getattr(appointment, 'queue_token', None)
    payment = getattr(appointment, 'payment', None)
    medical_record = getattr(appointment, 'medical_record', None)
    review = getattr(appointment, 'review', None)

    return render(request, 'clinic/appointment_detail.html', {
        'appointment': appointment,
        'token': token,
        'payment': payment,
        'medical_record': medical_record,
        'review': review,
    })


@login_required
def reschedule_appointment(request, appointment_id):
    """Patient reschedules their appointment to a new date/time."""
    appointment = get_object_or_404(
        Appointment, id=appointment_id, patient=request.user
    )

    # Can only reschedule PENDING or CONFIRMED appointments
    if appointment.status not in ('PENDING', 'CONFIRMED'):
        messages.error(request, "This appointment cannot be rescheduled.")
        return redirect('clinic:my_appointments')

    if request.method == 'POST':
        new_date = request.POST.get('appointment_date')
        new_time = request.POST.get('time_slot')

        if not new_date or not new_time:
            messages.error(request, "Please select both date and time.")
            return redirect('clinic:reschedule_appointment', appointment_id=appointment.id)

        from datetime import date as date_type
        from datetime import datetime

        try:
            parsed_date = datetime.strptime(new_date, '%Y-%m-%d').date()
        except ValueError:
            messages.error(request, "Invalid date format.")
            return redirect('clinic:reschedule_appointment', appointment_id=appointment.id)

        if parsed_date < date_type.today():
            messages.error(request, "Cannot reschedule to a past date.")
            return redirect('clinic:reschedule_appointment', appointment_id=appointment.id)

        # Check for conflicts (exclude current appointment)
        from django.db.models import Q
        conflict = Appointment.objects.filter(
            doctor=appointment.doctor,
            appointment_date=parsed_date,
            time_slot=new_time
        ).exclude(id=appointment.id).exists()

        if conflict:
            messages.error(
                request,
                "This time slot is already booked for the selected doctor. Please choose another."
            )
            return redirect('clinic:reschedule_appointment', appointment_id=appointment.id)

        # Update appointment
        appointment.appointment_date = parsed_date
        appointment.time_slot = new_time
        appointment.reason = request.POST.get('reason', appointment.reason)
        appointment.save()

        messages.success(
            request,
            f"Appointment rescheduled to {parsed_date.strftime('%B %d, %Y')} at {new_time}."
        )
        return redirect('clinic:my_appointments')

    return render(request, 'clinic/reschedule_appointment.html', {
        'appointment': appointment,
        'today': date.today(),
    })