import logging
from odoo import models, fields, api, _
from odoo.exceptions import UserError
from datetime import datetime
from itertools import permutations

_logger = logging.getLogger(__name__)

class StockPicking(models.Model):
    _inherit = 'stock.picking'

    optimized_sequence = fields.Integer(
        string='Optimized Sequence',
        help='Sequence number after route optimization',
        copy=False
    )
    
    distance_from_warehouse = fields.Float(
        string='Distance from Warehouse (miles)',
        help='Distance from warehouse to delivery address',
        copy=False,
        readonly=True,
        digits=(16, 2)  # 2 decimal places for miles
    )

    total_route_distance = fields.Float(
        string='Total Route Distance (miles)',
        help='Total distance of the optimized route including return to warehouse',
        copy=False,
        readonly=True,
        digits=(16, 2)
    )

    def _validate_address(self, partner):
        """Validate if a partner has a complete address"""
        if not partner:
            return False
        required_fields = ['street', 'city', 'zip']
        missing_fields = [field for field in required_fields if not partner[field]]
        if missing_fields:
            _logger.warning(f"Partner {partner.name} is missing fields: {', '.join(missing_fields)}")
            return False
        
        # Log the complete address for debugging
        _logger.info(f"Valid address for {partner.name}: {partner.street}, {partner.city}, {partner.zip}")
        return True

    def _meters_to_miles(self, meters):
        """Convert meters to miles"""
        return meters * 0.000621371  # Standard conversion factor

    def _calculate_route_distance(self, route, distance_matrix):
        """Calculate total distance for a given route including return to warehouse"""
        if not distance_matrix or not distance_matrix.get('rows'):
            raise UserError(_("Invalid distance matrix received from Google Maps API."))

        total_distance = 0
        current_point = 0  # warehouse is point 0
        
        # Add up distances between each point in the route
        for next_point in route:
            try:
                distance_element = distance_matrix['rows'][current_point]['elements'][next_point]
                if distance_element.get('status') != 'OK':
                    _logger.warning(f"Distance calculation failed between points {current_point} and {next_point}")
                    raise UserError(_("Could not calculate distance between some locations. Please verify addresses."))
                total_distance += distance_element['distance']['value']
                current_point = next_point
            except (KeyError, IndexError) as e:
                _logger.error(f"Error processing distance matrix: {str(e)}")
                raise UserError(_("Invalid response format from Google Maps API."))
        
        # Add return distance to warehouse
        try:
            return_element = distance_matrix['rows'][current_point]['elements'][0]
            if return_element.get('status') != 'OK':
                _logger.warning(f"Return distance calculation failed from point {current_point}")
                raise UserError(_("Could not calculate return distance to warehouse. Please verify addresses."))
            total_distance += return_element['distance']['value']
        except (KeyError, IndexError) as e:
            _logger.error(f"Error processing return distance: {str(e)}")
            raise UserError(_("Invalid response format for return distance calculation."))
        
        return self._meters_to_miles(total_distance)  # Convert meters to miles

    def _optimize_delivery_route(self, deliveries):
        """Optimize delivery route using Google Maps API"""
        if not deliveries:
            return False

        # Group deliveries by scheduled date
        deliveries_by_date = {}
        for delivery in deliveries:
            scheduled_date = fields.Date.to_string(delivery.scheduled_date)
            if scheduled_date not in deliveries_by_date:
                deliveries_by_date[scheduled_date] = []
            deliveries_by_date[scheduled_date].append(delivery)

        # Get warehouse address as starting point
        warehouse = self.env['stock.warehouse'].search([('company_id', '=', self.company_id.id)], limit=1)
        if not warehouse or not warehouse.partner_id:
            raise UserError(_("Please configure warehouse address first."))

        _logger.info(f"Checking warehouse address: {warehouse.name}")
        if not self._validate_address(warehouse.partner_id):
            raise UserError(_("Warehouse address is incomplete. Please add street, city, and ZIP code."))

        # Process each day's deliveries separately
        for date, day_deliveries in deliveries_by_date.items():
            _logger.info(f"\nProcessing deliveries for date: {date}")
            _logger.info(f"Total deliveries before validation: {len(day_deliveries)}")
            
            # Filter out deliveries without valid addresses
            valid_deliveries = []
            for delivery in day_deliveries:
                _logger.info(f"\nChecking delivery {delivery.name}:")
                if not delivery.partner_id:
                    _logger.warning(f"Delivery {delivery.name} has no partner")
                    continue
                
                _logger.info(f"Checking address for partner: {delivery.partner_id.name}")
                if not self._validate_address(delivery.partner_id):
                    _logger.warning(f"Delivery {delivery.name} has incomplete address")
                    continue
                valid_deliveries.append(delivery)

            _logger.info(f"Valid deliveries after address validation: {len(valid_deliveries)}")

            if not valid_deliveries:
                _logger.warning(f"No valid delivery addresses found for date {date}")
                continue

            # Get all delivery addresses including warehouse
            addresses = [warehouse.partner_id] + [d.partner_id for d in valid_deliveries]
            
            _logger.info("\nAll addresses being used for optimization:")
            for i, addr in enumerate(addresses):
                _logger.info(f"{i}. {addr.name}: {addr.street}, {addr.city}, {addr.zip}")

            if len(addresses) < 2:  # warehouse + at least one delivery
                _logger.warning("Not enough valid addresses for optimization")
                continue

            # Build complete distance matrix
            google_helper = self.env['google.maps.helper']
            distance_matrix = {'rows': []}

            # Get distances from each point to all other points
            for origin in addresses:
                _logger.info(f"\nRequesting distances from: {origin.name}")
                result = google_helper.get_distance_matrix(origin, addresses)
                
                if not result or result.get('status') != 'OK':
                    _logger.error(f"Failed to get distances from {origin.name}")
                    raise UserError(_("Failed to get distance matrix from Google Maps API."))
                
                if not result.get('rows') or not result['rows'][0].get('elements'):
                    _logger.error(f"Invalid response format for {origin.name}")
                    raise UserError(_("Invalid response format from Google Maps API."))
                
                distance_matrix['rows'].append(result['rows'][0])

            _logger.info("\nComplete distance matrix built successfully")

            # Try all possible routes if few enough deliveries (<=8)
            # Otherwise use a simpler nearest-neighbor approach
            delivery_count = len(valid_deliveries)
            _logger.info(f"Optimizing route for {delivery_count} deliveries")

            if delivery_count <= 8:
                # Generate all possible routes (excluding warehouse which is always start/end)
                possible_routes = list(permutations(range(1, delivery_count + 1)))
                best_route = None
                min_total_distance = float('inf')
                
                _logger.info(f"\nTesting all possible routes for {delivery_count} deliveries:")
                _logger.info("Warehouse is point 0")
                for addr_idx, addr in enumerate(addresses[1:], 1):
                    _logger.info(f"Point {addr_idx}: {addr.name} - {addr.street}, {addr.city}")
                
                # Find the route with minimum total distance
                route_number = 1
                for route in possible_routes:
                    try:
                        total_distance = self._calculate_route_distance(route, distance_matrix)
                        route_str = f"0 -> {' -> '.join(map(str, route))} -> 0"
                        _logger.info(f"Route {route_number}: {route_str} = {total_distance:.2f} miles")
                        if total_distance < min_total_distance:
                            min_total_distance = total_distance
                            best_route = route
                            _logger.info(f"★ New best route found! Distance: {total_distance:.2f} miles")
                        route_number += 1
                    except UserError as e:
                        _logger.warning(f"Route calculation failed: {str(e)}")
                        continue  # Skip invalid routes
            else:
                # Use nearest neighbor algorithm for larger sets
                unvisited = list(range(1, delivery_count + 1))
                current = 0  # Start at warehouse
                best_route = []
                
                while unvisited:
                    # Find nearest unvisited point
                    try:
                        next_point = min(
                            unvisited,
                            key=lambda x: distance_matrix['rows'][current]['elements'][x]['distance']['value']
                        )
                        best_route.append(next_point)
                        current = next_point
                        unvisited.remove(next_point)
                    except (KeyError, IndexError) as e:
                        _logger.error(f"Error in nearest neighbor calculation: {str(e)}")
                        raise UserError(_("Error calculating distances between locations."))

            if not best_route:
                _logger.error("No valid route found")
                raise UserError(_("Could not find a valid route. Please verify all delivery addresses are complete and valid."))

            # Calculate total route distance
            total_route_distance = self._calculate_route_distance(best_route, distance_matrix)
            _logger.info(f"Found route with total distance: {total_route_distance:.2f} miles")

            # Update delivery sequences and distances
            for sequence, point_index in enumerate(best_route, 1):
                delivery = valid_deliveries[point_index - 1]  # -1 because route indices start at 1
                try:
                    # Convert warehouse to delivery distance to miles
                    distance_from_warehouse = self._meters_to_miles(
                        distance_matrix['rows'][0]['elements'][point_index]['distance']['value']
                    )
                    _logger.info(f"Updating delivery {delivery.name} with sequence {sequence}")
                    delivery.write({
                        'optimized_sequence': sequence,
                        'distance_from_warehouse': distance_from_warehouse,
                        'total_route_distance': total_route_distance
                    })
                except (KeyError, IndexError) as e:
                    _logger.error(f"Error updating delivery {delivery.name}: {str(e)}")
                    raise UserError(_("Error updating delivery distances."))

        return True

    def action_optimize_route(self):
        """Manual trigger for route optimization"""
        self.ensure_one()
        if self.picking_type_id.code != 'outgoing':
            raise UserError(_("Route optimization is only available for delivery orders."))

        # Get all pending deliveries for the same company and scheduled date
        pending_deliveries = self.search([
            ('picking_type_id.code', '=', 'outgoing'),
            ('state', 'in', ['assigned', 'confirmed']),
            ('company_id', '=', self.company_id.id),
            ('scheduled_date', '>=', fields.Date.today()),
        ])

        if not pending_deliveries:
            raise UserError(_("No pending deliveries found to optimize."))

        try:
            self._optimize_delivery_route(pending_deliveries)
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Success'),
                    'message': _('Delivery routes have been optimized successfully.'),
                    'type': 'success',
                    'sticky': False,
                }
            }
        except Exception as e:
            _logger.error(f"Route optimization failed: {str(e)}")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': _('Error'),
                    'message': str(e),
                    'type': 'danger',
                    'sticky': True,
                }
            } 