# Lab Test Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from datetime import date

from ..models import LabTest, Appointment, PatientHistory
from backend.apps.accounts.models import User

from ..notification_service import create_notification


@login_required
def lab_tests(request):
    """Admin views and manages all lab tests."""
    if request.user.role == 'PATIENT':
        lab_tests = LabTest.objects.filter(patient=request.user).select_related('doctor')
    elif request.user.role == 'DOCTOR':
        # Doctors can only view (not edit) — read only
        lab_tests = LabTest.objects.filter(doctor=request.user).select_related('patient')
    else:
        # Admin sees all and can edit
        lab_tests = LabTest.objects.select_related('patient', 'doctor').all()

    # Filter by status
    status_filter = request.GET.get('status', '')
    if status_filter:
        lab_tests = lab_tests.filter(status=status_filter)

    # Pagination
    page = request.GET.get('page', 1)
    paginator = Paginator(lab_tests, 20)
    lab_tests_page = paginator.get_page(page)

    # Stats (use full queryset for counts)
    all_tests = LabTest.objects.all()
    stats = {
        'total': all_tests.count(),
        'ordered': all_tests.filter(status='ORDERED').count(),
        'in_lab': all_tests.filter(status__in=['SAMPLE_COLLECTED', 'IN_LAB']).count(),
        'results': all_tests.filter(status='RESULTS_RECEIVED').count(),
    }

    return render(request, 'clinic/lab_tests.html', {
        'lab_tests': lab_tests_page,
        'page_obj': lab_tests_page,
        'stats': stats,
        'status_filter': status_filter,
        'status_choices': LabTest.STATUS_CHOICES,
    })


@login_required
def update_lab_test(request, test_id):
    """Admin or Receptionist updates lab test status and uploads results."""
    if request.user.role not in ('ADMIN', 'RECEPTIONIST'):
        messages.error(request, "Only admin or receptionist can update lab tests.")
        return redirect('clinic:lab_tests')

    lab_test = get_object_or_404(LabTest, id=test_id)

    if request.method == 'POST':
        lab_test.status = request.POST.get('status', lab_test.status)
        lab_test.results = request.POST.get('results', lab_test.results)
        lab_test.notes = request.POST.get('notes', lab_test.notes)

        if request.POST.get('result_date'):
            lab_test.result_date = request.POST.get('result_date')
        elif lab_test.status == 'RESULTS_RECEIVED' and not lab_test.result_date:
            lab_test.result_date = date.today()

        # Handle file upload
        if request.FILES.get('report_file'):
            lab_test.report_file = request.FILES['report_file']

        lab_test.save()

        # Notify patient when results are received
        if lab_test.status == 'RESULTS_RECEIVED':
            create_notification(
                recipient=lab_test.patient,
                title="Lab Results Ready",
                message=f"Your {lab_test.test_name} lab test results are now available. Please visit the clinic or check your online records.",
                notification_type='LAB_RESULTS_READY',
            )

        messages.success(request, f"Lab test '{lab_test.test_name}' updated.")
        return redirect('clinic:lab_tests')

    return render(request, 'clinic/update_lab_test.html', {
        'lab_test': lab_test,
        'status_choices': LabTest.STATUS_CHOICES,
    })