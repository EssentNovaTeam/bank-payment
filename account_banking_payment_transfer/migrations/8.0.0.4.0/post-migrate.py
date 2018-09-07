# coding: utf-8
def migrate(cr, version):
    """ Fetch existing transfer move lines from the workflow trigger table """
    if not version:
        return
    cr.execute(
        """
        WITH rel AS (
            SELECT t.res_id AS move_line_id, i.res_id AS order_id
            FROM wkf_triggers t JOIN wkf_instance i ON t.instance_id = i.id
            WHERE t.model = 'account.move.line'
                 AND i.res_type = 'payment.order'
            GROUP BY move_line_id, order_id)
        INSERT INTO rel_payment_order_transfer_move_line
        (move_line_id, order_id) SELECT move_line_id, order_id FROM rel;
        """)
