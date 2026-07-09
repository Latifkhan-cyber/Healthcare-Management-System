# Notification Views
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.views.decorators.http import require_POST
from django.http import JsonResponse
from django.core.paginator import Paginator

from ..models import Notification
from backend.apps.accounts.models import User
from backend.apps.accounts.models import NotificationPreference

from ..notification_service import create_notification, resend_failed_notification, send_bulk_notification


@login_required
def notifications(request):
    """List all notifications for current user with pagination."""
    notifs = Notification.objects.filter(recipient=request.user)
    unread = notifs.filter(is_read=False).count()

    # Pagination - 15 per page
    paginator = Paginator(notifs, 15)
    page = request.GET.get('page', 1)
    notifs_page = paginator.get_page(page)

    return render(request, 'clinic/notifications.html', {
        'notifications': notifs_page,
        'page_obj': notifs_page,
        'unread_count': unread,
    })


@login_required
def mark_notification_read(request, notif_id):
    """Mark a single notification as read."""
    notif = get_object_or_404(
        Notification, id=notif_id, recipient=request.user
    )
    notif.is_read = True
    notif.save()
    return redirect('clinic:notifications')


@login_required
@require_POST
def mark_all_notifications_read(request):
    """Mark all notifications as read for current user."""
    count = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).update(is_read=True)
    messages.success(request, f"Marked {count} notification(s) as read.")
    return redirect('clinic:notifications')


@login_required
@require_POST
def delete_notification(request, notif_id):
    """Delete a single notification."""
    notif = get_object_or_404(
        Notification, id=notif_id, recipient=request.user
    )
    notif.delete()
    messages.success(request, "Notification deleted.")
    return redirect('clinic:notifications')


@login_required
@require_POST
def clear_all_notifications(request):
    """Delete all notifications for current user."""
    count = Notification.objects.filter(recipient=request.user).count()
    Notification.objects.filter(recipient=request.user).delete()
    messages.success(request, f"Cleared {count} notification(s).")
    return redirect('clinic:notifications')


@login_required
def notification_preferences(request):
    """Manage notification delivery preferences."""
    prefs, created = NotificationPreference.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        prefs.email_enabled = request.POST.get('email_enabled') == 'on'
        prefs.sms_enabled = request.POST.get('sms_enabled') == 'on'
        prefs.appointment_reminders = request.POST.get('appointment_reminders') == 'on'
        prefs.queue_notifications = request.POST.get('queue_notifications') == 'on'
        prefs.lab_results = request.POST.get('lab_results') == 'on'
        prefs.follow_up_reminders = request.POST.get('follow_up_reminders') == 'on'
        prefs.payment_notifications = request.POST.get('payment_notifications') == 'on'
        prefs.general_notifications = request.POST.get('general_notifications') == 'on'
        prefs.save()
        messages.success(request, "Notification preferences updated!")
        return redirect('clinic:notification_preferences')

    return render(request, 'clinic/notification_preferences.html', {
        'prefs': prefs,
    })


@login_required
def notifications_poll(request):
    """AJAX endpoint: Get unread notifications for real-time polling."""
    notifs = Notification.objects.filter(
        recipient=request.user, is_read=False
    ).order_by('-created_at')[:5]

    data = {
        'unread_count': Notification.objects.filter(recipient=request.user, is_read=False).count(),
        'notifications': [
            {
                'id': n.id,
                'title': n.title,
                'message': n.message[:100],
                'type': n.get_notification_type_display(),
                'time': n.created_at.isoformat(),
            }
            for n in notifs
        ]
    }
    return JsonResponse(data)


@login_required
def resend_notification(request, notif_id):
    """Admin: Resend a failed notification."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    notif = get_object_or_404(Notification, id=notif_id)
    resend_failed_notification(notif.id)
    messages.success(request, f"Notification #{notif_id} resent.")
    return redirect('clinic:failed_notifications')


@login_required
def failed_notifications(request):
    """Admin: View notifications with delivery failures."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    failed = Notification.objects.filter(
        delivery_error__isnull=False
    ).exclude(delivery_error='').order_by('-created_at')

    paginator = Paginator(failed, 20)
    page = request.GET.get('page', 1)
    failed_page = paginator.get_page(page)

    return render(request, 'clinic/failed_notifications.html', {
        'notifications': failed_page,
        'page_obj': failed_page,
    })


@login_required
def send_bulk_notification(request):
    """Admin: Send a notification to all patients."""
    if request.user.role != 'ADMIN':
        messages.error(request, "Access denied.")
        return redirect('dashboard')

    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        message = request.POST.get('message', '').strip()
        notification_type = request.POST.get('notification_type', 'GENERAL')
        send_email = request.POST.get('send_email') == 'on'
        send_sms = request.POST.get('send_sms') == 'on'
        recipient_type = request.POST.get('recipient_type', 'all')

        if not title or not message:
            messages.error(request, "Title and message are required.")
            return redirect('clinic:send_bulk_notification')

        # Get recipients
        if recipient_type == 'all':
            recipients = User.objects.filter(role='PATIENT')
        elif recipient_type == 'with_appointments':
            recipients = User.objects.filter(
                role='PATIENT',
                appointments__status='CONFIRMED'
            ).distinct()
        else:
            recipients = User.objects.filter(role='PATIENT')

        count = send_bulk_notification(
            recipients=recipients,
            title=title,
            message=message,
            notification_type=notification_type,
            send_email=send_email,
            send_sms=send_sms,
        )
        messages.success(request, f"Bulk notification sent to {count} patient(s).")
        return redirect('clinic:notifications')

    return render(request, 'clinic/send_bulk_notification.html')