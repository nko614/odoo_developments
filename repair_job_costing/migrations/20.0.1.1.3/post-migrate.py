def migrate(cr, version):
    # Part-cost lines of repairs (no project) got the default employee of the user who ended the repair.
    cr.execute("UPDATE account_analytic_line SET employee_id = NULL WHERE repair_id IS NOT NULL AND project_id IS NULL")
