# -*- coding: utf-8 -*-

from twisted.internet import defer
from twistar.dbobject import DBObject
from twistar.registry import Registry
from twistar.utils import createInstances

import logging
import queries


logger = logging.getLogger("synapse.persistence.pdu")


class PduDbEntry(DBObject):
    TABLENAME = "pdus"  # Table name

    def dict(self):
        return self.__dict__


class PduState(DBObject):
    TABLENAME = "state_pdu"  # Table name


class PduDestinationEntry(DBObject):
    TABLENAME = "pdu_destinations"  # Table name


class PduContextEdgesEntry(DBObject):
    TABLENAME = "pdu_context_edges"  # Table name


class PduContextForwardExtremeties(DBObject):
    TABLENAME = "pdu_context_forward_extremeties"  # Table name


@defer.inlineCallbacks
def register_new_outgoing_pdu(pdu):
    """ For outgoing pdus, we need to fill out the "previous_pdus" property.
        We do this by querying the current extremeties table.

        Does NOT remove existing extremeties, as we need to wait until
        we know the other side has actually received the pdu.
    """
    results = yield PduContextForwardExtremeties.findBy(context=pdu.context)

    pdu.previous_pdus = [(r.pdu_id, r.origin) for r in results]

    yield _add_pdu_to_tables(pdu)


@defer.inlineCallbacks
def register_remote_pdu(pdu):
    """ Called when we receive a remote pdu.

        Updates the edges + extremeties tables
    """
    yield _add_pdu_to_tables(pdu)

    if pdu.previous_pdus:
        yield _delete_forward_context_extremeties(
            pdu.context, pdu.previous_pdus)


@defer.inlineCallbacks
def register_pdu_as_sent(pdu):
    """ Called when we have succesfully sent the PDU, so it's safe to update
        the tables.

        Update extremeties table
    """
    if pdu.previous_pdus:
        yield _delete_forward_context_extremeties(
            pdu.context, pdu.previous_pdus)


@defer.inlineCallbacks
def _add_pdu_to_tables(pdu):
    """ Adds the pdu to the edges and extremeties table.
        DOES NOT DELETE existing extremeties. This should be done only when
        we know the remote sides have seen our message (for outgoing ones)
    """

    # Check to see if we have already received something that refrences this
    # pdu. If yes, we don't consider it an extremity
    result = yield PduContextEdgesEntry.findBy(
            prev_pdu_id=pdu.pdu_id,
            prev_origin=pdu.origin,
            context=pdu.context
        )

    if not result:
        # Add new pdu to extremeties table
        extrem = PduContextForwardExtremeties(
                pdu_id=pdu.pdu_id,
                origin=pdu.origin,
                context=pdu.context
            )

        yield extrem.save()

    # Update edges table with new pdu
    for r in pdu.previous_pdus:
        edge = PduContextForwardExtremeties(
                    pdu_id=pdu.pdu_id,
                    origin=pdu.origin,
                    prev_pdu_id=r[0],
                    prev_origin=r[1]
                )
        yield edge.save()


def get_pdus_after_transaction_id(origin, transaction_id, destination):
    """ Given a transaction_id, return all PDUs sent *after* that
        transaction_id to a given destination
    """
    query = queries.pdus_after_transaction_id()

    return _load_pdu_entries_from_query(query, origin, transaction_id,
                destination)


def get_state_pdus_for_context(context):
    """ Given a context, return all state pdus
    """
    query = queries.state_pdus_for_context()

    return _load_pdu_entries_from_query(query, context)


@defer.inlineCallbacks
def _load_pdu_entries_from_query(query, *args):
    """ Given the query that loads fetches rows of pdus from the db,
        actually load them as protocol.units.Pdu's
    """
    def interaction(txn):
        logger.debug("Exec %d bindings: %s" % (len(args), query))
        txn.execute(query, args)

        results = []
        for result in txn.fetchall():
            vals = Registry.getConfig().valuesToHash(
                txn, result, PduDbEntry.TABLENAME, False)
            results.append(vals)

        return results
        #return createInstances(results, PduDbEntry)

    results = yield Registry.DBPOOL.runInteraction(interaction)
    db_entries = yield createInstances(results, PduDbEntry)

    defer.returnValue(db_entries)


def _delete_forward_context_extremeties(context, pdu_list):
    query, where_args = queries.delete_forward_context_extremeties(
        context, pdu_list)

    return Registry.DBPOOL.runQuery(query, where_args)