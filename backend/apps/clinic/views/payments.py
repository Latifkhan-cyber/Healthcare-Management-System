# Payment Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Sum
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.conf import settings
from datetime import date

from ..models import (
    Appointment, QueueToken, PatientHistory,
    Payment, DoctorSchedule, Notification, DoctorReview, PaymentSettings
)
from ..forms import PaymentForm
from backend.apps.accounts.models import User

# Import shared helper
from .appointments import estimate_wait_time
from ..notification_service import create_notification


@login_required
def make_payment(request, appointment_id):
    appointment = get_object_or_404(
        Appointment, id=appointment_id, patient=request.user
    )

    if hasattr(appointment, 'payment'):
        return redirect('clinic:my_appointments')

    if request.method == 'POST':
        form = PaymentForm(request.POST, request.FILES)
        if form.is_valid():
            payment = form.save(commit=False)
            payment.appointment = appointment

            payment.status = 'PENDING'
            msg = "Payment proof submitted! Your appointment will be confirmed after admin verification."

            payment.save()
            appointment.save()

            messages.success(request, msg)
            return redirect('clinic:my_appointments')
    else:
        initial = {'amount': appointment.doctor.consultation_fee}
        form = PaymentForm(initial=initial)

    return render(request, 'clinic/make_payment.html', {
        'form': form,
        'appointment': appointment,
        'payment_settings': PaymentSettings.objects.first(),
    })


@login_required
def payment_records(request):
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    payments = Payment.objects.select_related(
        'appointment__patient', 'appointment__doctor'
    ).order_by('-created_at')

    # Pagination
    from django.core.paginator import Paginator
    page = request.GET.get('page', 1)
    paginator = Paginator(payments, 20)
    payments_page = paginator.get_page(page)

    stats = {
        'total_revenue': Payment.objects.filter(status='PAID').aggregate(
            Sum('amount'))['amount__sum'] or 0,
        'paid_count': Payment.objects.filter(status='PAID').count(),
        'pending_count': Payment.objects.filter(status='PENDING').count(),
        'failed_count': Payment.objects.filter(status='FAILED').count(),
        'refunded_count': Payment.objects.filter(status='REFUNDED').count(),
    }

    return render(request, 'clinic/payment_records.html', {
        'payments': payments_page,
        'page_obj': payments_page,
        **stats,
    })


@login_required
def verify_payment(request, payment_id):
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')
    payment = get_object_or_404(Payment, id=payment_id)
    payment.status = 'PAID'
    payment.save()
    appointment = payment.appointment
    appointment.status = 'CONFIRMED'
    appointment.save()

    # Generate queue token on confirmation
    existing_count = QueueToken.objects.filter(
        appointment__doctor=appointment.doctor,
        appointment__appointment_date=appointment.appointment_date,
        is_active=True
    ).count()
    token_number = existing_count + 1
    estimated_wait = estimate_wait_time(
        appointment.doctor, appointment.appointment_date
    )
    QueueToken.objects.create(
        appointment=appointment,
        token_number=token_number,
        estimated_wait_minutes=estimated_wait
    )

    # Notify patient
    create_notification(
        recipient=appointment.patient,
        title="Appointment Confirmed",
        message=f"Your appointment with Dr. {appointment.doctor.get_full_name()} on {appointment.appointment_date.strftime('%B %d, %Y')} at {appointment.time_slot} has been confirmed. Your queue token is #{token_number}.",
        notification_type='APPOINTMENT_CONFIRMED',
    )

    messages.success(request, "Payment verified. Appointment confirmed.")
    return redirect('clinic:payment_records')


@login_required
def reject_payment(request, payment_id):
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')
    payment = get_object_or_404(Payment, id=payment_id)
    payment.status = 'FAILED'
    payment.save()
    messages.warning(request, "Payment rejected.")
    return redirect('clinic:payment_records')


@login_required
def payment_receipt(request, payment_id):
    """Generate a PDF receipt for a payment."""
    payment = get_object_or_404(Payment, id=payment_id)

    # Access control: patient sees own, admin/receptionist sees all
    if request.user.role == 'PATIENT' and payment.appointment.patient != request.user:
        messages.error(request, "Access denied.")
        return redirect('dashboard')
    if request.user.role == 'DOCTOR':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    if payment.status != 'PAID':
        messages.error(request, "Receipt is only available for paid payments.")
        return redirect('clinic:payment_records')

    # Generate PDF using weasyprint
    from weasyprint import HTML

    context = {
        'payment': payment,
        'appointment': payment.appointment,
        'patient': payment.appointment.patient,
        'doctor': payment.appointment.doctor,
        'payment_settings': PaymentSettings.objects.first(),
    }

    html_string = render_to_string('clinic/receipt_pdf.html', context)
    html = HTML(string=html_string, base_url=request.build_absolute_uri('/'))
    pdf_file = html.write_pdf()

    response = HttpResponse(pdf_file, content_type='application/pdf')
    filename = f"Healthcare_Receipt_{payment.id}_{payment.appointment.patient.patient_id or 'N/A'}.pdf"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
def process_refund(request, payment_id):
    """Admin processes a refund for a paid payment."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    payment = get_object_or_404(Payment, id=payment_id, status='PAID')

    if request.method == 'POST':
        refund_amount = request.POST.get('refund_amount', '')
        refund_reason = request.POST.get('refund_reason', '')

        try:
            refund_amount = float(refund_amount)
        except (ValueError, TypeError):
            messages.error(request, "Invalid refund amount.")
            return redirect('clinic:process_refund', payment_id=payment.id)

        if refund_amount <= 0 or refund_amount > float(payment.amount):
            messages.error(request, f"Refund amount must be between 0 and {payment.amount}.")
            return redirect('clinic:process_refund', payment_id=payment.id)

        # Process the refund
        payment.status = 'REFUNDED'
        payment.notes = f"{payment.notes or ''}\n\nREFUND PROCESSED:\nAmount: PKR {refund_amount}\nReason: {refund_reason}\nDate: {date.today()}"
        payment.save()

        # Cancel the associated appointment
        appointment = payment.appointment
        appointment.status = 'CANCELLED'
        appointment.save()

        # Deactivate queue token if exists
        if hasattr(appointment, 'queue_token'):
            appointment.queue_token.is_active = False
            appointment.queue_token.save()

        messages.success(
            request,
            f"Refund of PKR {refund_amount} processed for {payment.appointment.patient.get_full_name()}. "
            f"Appointment cancelled."
        )
        return redirect('clinic:payment_records')

    return render(request, 'clinic/process_refund.html', {
        'payment': payment,
    })


@login_required
def patient_payment_history(request):
    """Patient views their own complete payment history."""
    if request.user.role != 'PATIENT':
        # Admin/receptionist redirect to records
        return redirect('clinic:payment_records')

    payments = Payment.objects.filter(
        appointment__patient=request.user
    ).select_related('appointment__doctor').order_by('-created_at')

    # Stats
    stats = {
        'total_payments': payments.count(),
        'total_paid': payments.filter(status='PAID').aggregate(s=Sum('amount'))['s'] or 0,
        'total_pending': payments.filter(status='PENDING').aggregate(s=Sum('amount'))['s'] or 0,
        'total_refunded': payments.filter(status='REFUNDED').aggregate(s=Sum('amount'))['s'] or 0,
    }

    return render(request, 'clinic/patient_payment_history.html', {
        'payments': payments,
        'stats': stats,
    })


@login_required
def confirm_cash_payment(request, payment_id):
    """Admin confirms cash payment received at clinic."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')
    payment = get_object_or_404(Payment, id=payment_id, method='CASH', status='PENDING')
    payment.status = 'PAID'
    payment.save()
    appointment = payment.appointment
    appointment.status = 'CONFIRMED'
    appointment.save()

    # Generate queue token
    existing_count = QueueToken.objects.filter(
        appointment__doctor=appointment.doctor,
        appointment__appointment_date=appointment.appointment_date,
        is_active=True
    ).count()
    token_number = existing_count + 1
    estimated_wait = estimate_wait_time(
        appointment.doctor, appointment.appointment_date
    )
    QueueToken.objects.create(
        appointment=appointment,
        token_number=token_number,
        estimated_wait_minutes=estimated_wait
    )

    # Notify patient
    create_notification(
        recipient=appointment.patient,
        title="Appointment Confirmed",
        message=f"Your walk-in appointment with Dr. {appointment.doctor.get_full_name()} has been confirmed. Queue token: #{token_number}.",
        notification_type='APPOINTMENT_CONFIRMED',
    )

    messages.success(request, f"Cash payment confirmed for {appointment.patient.get_full_name()}. Appointment is now confirmed.")
    return redirect('clinic:payment_records')


@login_required
def payment_settings(request):
    """Admin manages clinic payment account details."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    settings_obj, _ = PaymentSettings.objects.get_or_create(id=1)

    if request.method == 'POST':
        settings_obj.jazzcash_number = request.POST.get('jazzcash_number', '')
        settings_obj.easypaisa_number = request.POST.get('easypaisa_number', '')
        settings_obj.bank_name = request.POST.get('bank_name', '')
        settings_obj.bank_account_title = request.POST.get('bank_account_title', '')
        settings_obj.bank_account_number = request.POST.get('bank_account_number', '')
        settings_obj.save()
        messages.success(request, "Payment settings updated successfully!")
        return redirect('clinic:payment_settings')

    return render(request, 'clinic/payment_settings.html', {
        'settings': settings_obj,
    })