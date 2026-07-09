# Doctor Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Avg

from ..models import DoctorSchedule, DoctorReview, Appointment
from ..forms import DoctorScheduleForm, DoctorReviewForm
from backend.apps.accounts.models import User


@login_required
def manage_doctors(request):
    if request.user.role != 'ADMIN':
        return redirect('dashboard')
    doctors = User.objects.filter(role='DOCTOR')
    return render(request, 'clinic/manage_doctors.html', {
        'doctors': doctors,
    })


@login_required
def doctor_schedule(request, doctor_id):
    if request.user.role != 'ADMIN':
        return redirect('dashboard')
    doctor = get_object_or_404(User, id=doctor_id, role='DOCTOR')
    schedules = DoctorSchedule.objects.filter(doctor=doctor)

    if request.method == 'POST':
        form = DoctorScheduleForm(request.POST)
        if form.is_valid():
            sched = form.save(commit=False)
            sched.doctor = doctor
            sched.save()
            messages.success(request, "Schedule updated.")
            return redirect('clinic:doctor_schedule', doctor_id=doctor.id)
    else:
        form = DoctorScheduleForm()

    return render(request, 'clinic/doctor_schedule.html', {
        'doctor': doctor,
        'schedules': schedules,
        'form': form,
    })


@login_required
def review_doctor(request, appointment_id):
    if request.user.role != 'PATIENT':
        return redirect('dashboard')
    appointment = get_object_or_404(
        Appointment, id=appointment_id, patient=request.user, status='COMPLETED'
    )
    if hasattr(appointment, 'review'):
        messages.info(request, "You have already reviewed this appointment.")
        return redirect('clinic:my_appointments')

    if request.method == 'POST':
        form = DoctorReviewForm(request.POST)
        if form.is_valid():
            review = form.save(commit=False)
            review.patient = request.user
            review.doctor = appointment.doctor
            review.appointment = appointment
            review.save()
            messages.success(request, "Thank you for your review!")
            return redirect('clinic:my_appointments')
    else:
        form = DoctorReviewForm()

    return render(request, 'clinic/review_doctor.html', {
        'form': form,
        'appointment': appointment,
    })


@login_required
def doctor_reviews(request, doctor_id):
    doctor = get_object_or_404(User, id=doctor_id, role='DOCTOR')
    reviews = DoctorReview.objects.filter(
        doctor=doctor
    ).select_related('patient').order_by('-created_at')
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg']
    return render(request, 'clinic/doctor_reviews.html', {
        'doctor': doctor,
        'reviews': reviews,
        'avg_rating': avg_rating,
    })


def doctor_profile(request, doctor_id):
    """Public-facing doctor profile with reviews and schedule."""
    doctor = get_object_or_404(User, id=doctor_id, role='DOCTOR')
    reviews = DoctorReview.objects.filter(
        doctor=doctor
    ).select_related('patient').order_by('-created_at')
    avg_rating = reviews.aggregate(Avg('rating'))['rating__avg']
    return render(request, 'clinic/doctor_profile.html', {
        'doctor': doctor,
        'reviews': reviews,
        'avg_rating': avg_rating,
    })