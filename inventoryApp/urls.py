from django.urls import path
from . import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # =================== AUTHENTICATION ===================
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # =================== HOME / POS ===================
    path('home/', views.home, name='home'),
    path('receipt/<int:sale_id>/', views.view_receipt, name='view_receipt'),
    
    # =================== DASHBOARD ===================
    path('admin_dashboard/', views.admin_dashboard, name='admin_dashboard'),
    
    # =================== STAFF MANAGEMENT ===================
    path('register_staff/', views.register_staff, name='register_staff'),
    path('staff/', views.staff_list, name='staff_list'),
    path('staff/edit/', views.edit_staff, name='edit_staff'),
    path('sale_history/', views.sale_history, name='sale_history'),
    path('staff/delete/<int:pk>/', views.delete_staff, name='delete_staff'),
    
    # =================== PRODUCTS ===================
    path('products/', views.product_list, name='product_list'),
    path('products/add/', views.add_product, name='add_product'),
    path('products/edit/<int:pk>/', views.edit_product, name='edit_product'),
    path('products/delete/<int:pk>/', views.delete_product, name='delete_product'),
    
    # =================== DEBTORS ===================
    path('debtors/', views.debtors_list, name='debtors_list'),
    path('debtors/payment/<int:sale_id>/', views.record_payment, name='record_payment'),
    
    # =================== CARTS ===================
    path('saved-carts/', views.saved_carts_list, name='saved_carts_list'),
    path('saved-cart/<int:cart_id>/', views.view_saved_cart, name='view_saved_cart'),
    
    # =================== REFUND REQUESTS ===================
    path('refunds/', views.refund_list, name='refund_list'),
    path('refund-requests/', views.refund_requests_list, name='refund_requests_list'),
    path('refund-requests/create/', views.create_refund_request, name='create_refund_request'),
    path('refund-requests/edit/<int:pk>/', views.edit_refund_request, name='edit_refund_request'),
    path('refund-requests/approve/<int:pk>/', views.approve_refund_request, name='approve_refund_request'),
    path('refund-requests/decline/<int:pk>/', views.decline_refund_request, name='decline_refund_request'),
    # =================== API ENDPOINTS ===================
    path('api/get-customer-sales/', views.get_customer_sales, name='get_customer_sales'),
    path('api/refund-stats/', views.get_refund_stats, name='get_refund_stats'),
    path('api/refund-details/<int:pk>/', views.refund_details_api, name='refund_details_api'),
    
    # =================== NOTIFICATION API ===================
    path('api/notification-counts/', views.notification_counts_api, name='notification_counts_api'),
    path('mark-notifications-read/', views.mark_notifications_read, name='mark_notifications_read'),
    
    # =================== SALES API ===================
    # POS API
    path('api/search-products/', views.search_products_api, name='search_products'),
    path('api/process-sale/', views.process_sale, name='process_sale'),
    
    
    # Cart API
    path('api/save-pending-cart/', views.save_pending_cart, name='save_pending_cart'),
    path('api/load-pending-cart/', views.load_pending_cart, name='load_pending_cart'),
    path('api/delete-pending-cart/', views.delete_pending_cart, name='delete_pending_cart'),
    path('api/save-cart/', views.save_cart, name='save_cart'),
    path('api/load-saved-cart/<int:cart_id>/', views.load_saved_cart, name='load_saved_cart'),
    path('api/delete-saved-cart/<int:cart_id>/', views.delete_saved_cart, name='delete_saved_cart'),
    path('api/profit-stats/', views.profit_stats_api, name='profit_stats_api'),
    
    # =================== REAL-TIME SEARCH API ENDPOINTS ===================
    path('api/search/sales/', views.search_sales_api, name='search_sales'),
    path('api/search/stock/', views.search_stock_api, name='search_stock'),
    path('api/search/products/', views.search_products_api, name='search_products_api'),
    path('api/search/staff/', views.search_staff_api, name='search_staff'),
    path('api/search/debtors/', views.search_debtors_api, name='search_debtors'),
    path('api/sales-history/', views.sales_history_api, name='sales_history_api'),
    
    path('api/recent-sales-stats/', views.recent_sales_stats_api, name='recent_sales_stats'),
    path('api/recent-sales/', views.recent_sales_api, name='recent_sales'),
    path('api/search-recent-sales/', views.search_recent_sales_api, name='search_recent_sales'),
    path('api/all-sales/', views.all_sales_api, name='all_sales'),
    path('api/search-all-sales/', views.search_all_sales_api, name='search_all_sales'),
    path('api/sale-details/<int:pk>/', views.sale_details_api, name='sale_details_api'),


    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/add/', views.add_supplier, name='add_supplier'),
    path('suppliers/edit/<int:pk>/', views.edit_supplier, name='edit_supplier'),
    path('suppliers/delete/<int:pk>/', views.delete_supplier, name='delete_supplier'),
    
    # Customer URLs
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/add/', views.add_customer, name='add_customer'),
    path('customers/edit/<int:pk>/', views.edit_customer, name='edit_customer'),
    path('customers/delete/<int:pk>/', views.delete_customer, name='delete_customer'),
    

    path('api/expiring-products/', views.expiring_products_api, name='expiring_products_api'),
]

# Serve media files in development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)