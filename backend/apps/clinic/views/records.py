# Medical Records Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.core.paginator import Paginator
from datetime import date

from ..models import PatientHistory, Appointment, LabTest, DoctorReview
from ..forms import PatientHistoryForm
from backend.apps.accounts.models import User

from ..notification_service import create_notification


@login_required
def view_history(request):
    if request.user.role == 'PATIENT':
        records = PatientHistory.objects.filter(
            patient=request.user
        ).select_related('doctor')
    elif request.user.role == 'DOCTOR':
        records = PatientHistory.objects.filter(
            doctor=request.user
        ).select_related('patient')
    else:
        records = PatientHistory.objects.select_related('patient', 'doctor')

    page = request.GET.get('page', 1)
    paginator = Paginator(records, 15)
    records_page = paginator.get_page(page)

    return render(request, 'clinic/view_history.html', {
        'records': records_page,
        'page_obj': records_page,
    })


@login_required
def view_follow_ups(request):
    today = date.today()
    base_qs = PatientHistory.objects.filter(
        follow_up_required=True
    ).select_related('patient', 'doctor')

    if request.user.role == 'PATIENT':
        follow_ups = base_qs.filter(patient=request.user)
    elif request.user.role == 'DOCTOR':
        follow_ups = base_qs.filter(doctor=request.user)
    else:
        follow_ups = base_qs

    follow_ups = follow_ups.order_by('follow_up_date')
    return render(request, 'clinic/view_follow_ups.html', {
        'follow_ups': follow_ups,
        'today': today,
    })


@login_required
def print_medical_record(request, history_id):
    """Generate a PDF medical record/prescription."""
    record = get_object_or_404(PatientHistory, id=history_id)

    # Access control
    if request.user.role == 'PATIENT' and record.patient != request.user:
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    from weasyprint import HTML

    context = {
        'record': record,
        'patient': record.patient,
        'doctor': record.doctor,
        'appointment': record.appointment,
    }

    html_string = render_to_string('clinic/medical_record_pdf.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
    pdf_file = html.write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    filename = f"Medical_Record_{record.patient.patient_id or 'N/A'}_{record.visit_date}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def record_consultation(request, appointment_id):
    """Admin or Receptionist records consultation details."""
    if request.user.role not in ('ADMIN', 'RECEPTIONIST'):
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    appointment = get_object_or_404(Appointment, id=appointment_id)

    # Check if already recorded
    if hasattr(appointment, 'medical_record'):
        messages.info(request, "Consultation already recorded for this appointment.")
        return redirect('clinic:patient_detail', patient_id=appointment.patient.patient_id)

    if request.method == 'POST':
        form = PatientHistoryForm(request.POST, request.FILES)
        if form.is_valid():
            history = form.save(commit=False)
            history.patient = appointment.patient
            history.doctor = appointment.doctor
            history.appointment = appointment
            history.save()

            # Create LabTest records if tests were ordered
            lab_tests_text = form.cleaned_data.get('lab_tests_ordered', '').strip()
            if lab_tests_text:
                import re
                test_names = [t.strip() for t in re.split(r'[\n,]+', lab_tests_text) if t.strip()]
                for test_name in test_names:
                    LabTest.objects.create(
                        patient=appointment.patient,
                        doctor=appointment.doctor,
                        history=history,
                        test_name=test_name,
                        status='ORDERED',
                    )

            appointment.status = 'COMPLETED'
            appointment.save()

            pid = appointment.patient.patient_id or "N/A"
            msg = f"Consultation recorded for {appointment.patient.get_full_name()} (ID: {pid})."
            if lab_tests_text:
                msg += f" {len(test_names)} lab test(s) ordered."

            # Notify patient about follow-up
            if form.cleaned_data.get('follow_up_required'):
                follow_up_date = form.cleaned_data.get('follow_up_date')
                if follow_up_date:
                    create_notification(
                        recipient=appointment.patient,
                        title="Follow-Up Reminder",
                        message=f"Dr. {appointment.doctor.get_full_name()} has scheduled a follow-up for {follow_up_date.strftime('%B %d, %Y')}. Please book an appointment.",
                        notification_type='FOLLOW_UP_DUE',
                    )

            messages.success(request, msg)
            return redirect('clinic:patient_detail', patient_id=appointment.patient.patient_id)
    else:
        form = PatientHistoryForm()

    return render(request, 'clinic/record_consultation.html', {
        'form': form,
        'appointment': appointment,
    })