"""
Load Testing Harness for Prisoner's Dilemma Application

This script simulates concurrent users performing various operations to
test application performance under load. It supports multiple test scenarios
and generates detailed performance reports.

Usage:
    python scripts/load_test.py --users 10 --duration 30 --scenario normal
    python scripts/load_test.py --users 50 --duration 60 --scenario peak
    python scripts/load_test.py --users 100 --duration 120 --scenario stress
    python scripts/load_test.py --users 200 --duration 180 --scenario stress
    python scripts/load_test.py --scenario spike --duration 60
    python scripts/load_test.py --scenario endurance --duration 600

Requirements:
    - Flask application must be running (default: http://localhost:5000)
    - Test users must exist or signup must be enabled
    - For full testing, admin users may be needed

Options:
    --users N           Number of concurrent users (default: 10)
    --duration N        Duration in seconds (default: 60)
    --scenario NAME     Test scenario: normal, peak, stress, spike, endurance (default: normal)
    --base-url URL      Base URL of the application (default: http://localhost:5000)
    --output FILE       Output file for detailed results (default: none)
    --mock-auth         Skip real authentication (for isolated performance testing)
"""

import argparse
import time
import threading
import requests
import random
import sys
import json
from statistics import mean, median, stdev
from collections import defaultdict
from datetime import datetime
from urllib.parse import urljoin

class PerformanceMetrics:
    """Collect and calculate performance metrics"""
    
    def __init__(self):
        self.response_times = defaultdict(list)
        self.status_codes = defaultdict(list)
        self.errors = []
        self.start_time = None
        self.end_time = None
        self.lock = threading.Lock()
        
    def record_request(self, endpoint, response_time, status_code, error=None):
        """Record a single request's metrics"""
        with self.lock:
            self.response_times[endpoint].append(response_time)
            self.status_codes[endpoint].append(status_code)
            if error:
                self.errors.append({
                    'endpoint': endpoint,
                    'error': str(error),
                    'time': time.time()
                })
    
    def calculate_percentile(self, data, percentile):
        """Calculate percentile from sorted data"""
        if not data:
            return 0
        sorted_data = sorted(data)
        index = int(len(sorted_data) * percentile / 100)
        return sorted_data[min(index, len(sorted_data) - 1)]
    
    def get_summary(self):
        """Generate performance summary"""
        duration = self.end_time - self.start_time if self.end_time and self.start_time else 0
        total_requests = sum(len(times) for times in self.response_times.values())
        
        summary = {
            'duration': duration,
            'total_requests': total_requests,
            'throughput': total_requests / duration if duration > 0 else 0,
            'error_count': len(self.errors),
            'error_rate': len(self.errors) / total_requests if total_requests > 0 else 0,
            'endpoints': {}
        }
        
        for endpoint, times in self.response_times.items():
            if times:
                summary['endpoints'][endpoint] = {
                    'count': len(times),
                    'mean': mean(times),
                    'median': median(times),
                    'p50': self.calculate_percentile(times, 50),
                    'p90': self.calculate_percentile(times, 90),
                    'p95': self.calculate_percentile(times, 95),
                    'p99': self.calculate_percentile(times, 99),
                    'min': min(times),
                    'max': max(times),
                    'stdev': stdev(times) if len(times) > 1 else 0,
                    'success_rate': sum(1 for code in self.status_codes[endpoint] if 200 <= code < 300) / len(times)
                }
        
        return summary


class LoadTester:
    """Main load testing orchestrator"""
    
    def __init__(self, base_url, num_users, duration, scenario, mock_auth=False):
        self.base_url = base_url.rstrip('/')
        self.num_users = num_users
        self.duration = duration
        self.scenario = scenario
        self.mock_auth = mock_auth
        self.metrics = PerformanceMetrics()
        self.stop_flag = threading.Event()
        self.session = requests.Session()
        
        # Sample strategies for testing
        self.sample_strategies = [
            "def strategy(history):\n    return 'C'",
            "def strategy(history):\n    return 'D'",
            "def strategy(history):\n    if not history:\n        return 'C'\n    return history[-1][1]",
            "def strategy(history):\n    if not history:\n        return 'C'\n    return 'D' if history[-1][1] == 'D' else 'C'",
        ]
        
    def make_request(self, method, endpoint, **kwargs):
        """Make HTTP request and record metrics"""
        url = urljoin(self.base_url, endpoint)
        start_time = time.time()
        error = None
        status_code = 0
        
        try:
            if method == 'GET':
                response = self.session.get(url, timeout=30, **kwargs)
            elif method == 'POST':
                response = self.session.post(url, timeout=30, **kwargs)
            else:
                raise ValueError(f"Unsupported method: {method}")
            
            status_code = response.status_code
            response_time = time.time() - start_time
            
            return response, response_time, None
            
        except Exception as e:
            error = e
            response_time = time.time() - start_time
            return None, response_time, error
        finally:
            self.metrics.record_request(endpoint, time.time() - start_time, status_code, error)
    
    def user_flow_basic(self, user_id):
        """Basic user flow: view pages and perform simple operations"""
        think_time = random.uniform(0.5, 2.0)
        
        while not self.stop_flag.is_set():
            # Home page
            self.make_request('GET', '/')
            time.sleep(think_time)
            
            if self.stop_flag.is_set():
                break
            
            # Tournament page
            self.make_request('GET', '/tournament')
            time.sleep(think_time)
            
            if self.stop_flag.is_set():
                break
            
            # Results page
            self.make_request('GET', '/results')
            time.sleep(think_time)
    
    def user_flow_authenticated(self, user_id):
        """Authenticated user flow: login and perform operations"""
        think_time = random.uniform(0.5, 2.0)
        
        # Mock authentication by setting session
        if self.mock_auth:
            # Simulate authenticated requests with session cookie
            pass
        
        iteration = 0
        while not self.stop_flag.is_set():
            iteration += 1
            
            # Check auth status
            self.make_request('GET', '/check-auth')
            time.sleep(think_time / 2)
            
            if self.stop_flag.is_set():
                break
            
            # Load strategies
            self.make_request('GET', '/load-strategies')
            time.sleep(think_time)
            
            if self.stop_flag.is_set():
                break
            
            # Save a strategy (occasionally)
            if iteration % 3 == 0:
                strategy_code = random.choice(self.sample_strategies)
                self.make_request('POST', '/save-strategy', json={
                    'name': f'LoadTest_{user_id}_{iteration}',
                    'code': strategy_code
                })
                time.sleep(think_time)
            
            if self.stop_flag.is_set():
                break
            
            # Play a game (occasionally)
            if iteration % 2 == 0:
                self.make_request('POST', '/play', json={
                    'player1_strategy': self.sample_strategies[0],
                    'player2_strategy': self.sample_strategies[1],
                    'rounds': 10
                })
                time.sleep(think_time * 1.5)
    
    def user_flow_tournament(self, user_id):
        """Tournament-focused user flow"""
        think_time = random.uniform(0.5, 2.0)
        
        iteration = 0
        while not self.stop_flag.is_set():
            iteration += 1
            
            # Load tournament strategy
            self.make_request('GET', '/load-tournament-strategy')
            time.sleep(think_time)
            
            if self.stop_flag.is_set():
                break
            
            # View tournament results (occasionally)
            if iteration % 2 == 0:
                self.make_request('GET', '/api/tournaments')
                time.sleep(think_time)
            
            if self.stop_flag.is_set():
                break
            
            # Save tournament strategy (occasionally)
            if iteration % 4 == 0:
                strategy_code = random.choice(self.sample_strategies)
                self.make_request('POST', '/save-tournament-strategy', json={
                    'code': strategy_code
                })
                time.sleep(think_time)
    
    def user_flow_mixed(self, user_id):
        """Mixed user flow combining different operations"""
        flows = [self.user_flow_basic, self.user_flow_authenticated, self.user_flow_tournament]
        selected_flow = random.choice(flows)
        selected_flow(user_id)
    
    def spike_pattern(self):
        """Spike test: gradual increase then sudden spike"""
        # Phase 1: Start with 25% of users (30 seconds)
        initial_users = max(1, self.num_users // 4)
        print(f"  Phase 1: Starting with {initial_users} users...")
        threads = []
        
        for i in range(initial_users):
            thread = threading.Thread(target=self.user_flow_mixed, args=(i,))
            thread.daemon = True
            thread.start()
            threads.append(thread)
            time.sleep(0.1)
        
        time.sleep(30)
        
        # Phase 2: Sudden spike to full capacity
        print(f"  Phase 2: SPIKE to {self.num_users} users...")
        for i in range(initial_users, self.num_users):
            thread = threading.Thread(target=self.user_flow_mixed, args=(i,))
            thread.daemon = True
            thread.start()
            threads.append(thread)
            time.sleep(0.01)  # Very rapid addition
        
        # Phase 3: Sustain spike for remaining duration
        remaining_time = max(0, self.duration - 30)
        print(f"  Phase 3: Sustaining load for {remaining_time}s...")
        time.sleep(remaining_time)
        
        return threads
    
    def run_scenario(self):
        """Execute the configured test scenario"""
        print(f"\n{'='*70}")
        print(f"Starting Load Test")
        print(f"{'='*70}")
        print(f"  Scenario: {self.scenario}")
        print(f"  Users: {self.num_users}")
        print(f"  Duration: {self.duration}s")
        print(f"  Base URL: {self.base_url}")
        print(f"  Mock Auth: {self.mock_auth}")
        print(f"{'='*70}\n")
        
        self.metrics.start_time = time.time()
        threads = []
        
        try:
            if self.scenario == 'spike':
                threads = self.spike_pattern()
            else:
                # Normal, peak, stress, endurance scenarios
                # Determine user flow based on scenario
                if self.scenario == 'normal':
                    flow = self.user_flow_mixed
                    stagger_delay = 0.5
                elif self.scenario == 'peak':
                    flow = self.user_flow_authenticated
                    stagger_delay = 0.2
                elif self.scenario in ['stress', 'endurance']:
                    flow = self.user_flow_mixed
                    stagger_delay = 0.1
                else:
                    flow = self.user_flow_basic
                    stagger_delay = 0.5
                
                # Start all user threads
                print(f"Starting {self.num_users} concurrent users...")
                for i in range(self.num_users):
                    thread = threading.Thread(target=flow, args=(i,))
                    thread.daemon = True
                    thread.start()
                    threads.append(thread)
                    time.sleep(stagger_delay)
                
                # Run for specified duration
                print(f"Test in progress... (duration: {self.duration}s)")
                time.sleep(self.duration)
            
            # Signal all threads to stop
            print("\nStopping test...")
            self.stop_flag.set()
            
            # Wait for threads to finish (with timeout)
            for thread in threads:
                thread.join(timeout=5)
            
        except KeyboardInterrupt:
            print("\n\nTest interrupted by user!")
            self.stop_flag.set()
        finally:
            self.metrics.end_time = time.time()
    
    def generate_report(self, output_file=None):
        """Generate and display performance report"""
        summary = self.metrics.get_summary()
        
        print(f"\n{'='*70}")
        print(f"Load Test Results")
        print(f"{'='*70}")
        print(f"  Test Duration: {summary['duration']:.2f}s")
        print(f"  Total Requests: {summary['total_requests']}")
        print(f"  Throughput: {summary['throughput']:.2f} req/s")
        print(f"  Error Count: {summary['error_count']}")
        print(f"  Error Rate: {summary['error_rate']*100:.2f}%")
        print(f"{'='*70}\n")
        
        if summary['endpoints']:
            print(f"Per-Endpoint Performance:")
            print(f"{'-'*70}")
            
            for endpoint, metrics in sorted(summary['endpoints'].items()):
                print(f"\n  {endpoint}")
                print(f"    Count: {metrics['count']}")
                print(f"    Success Rate: {metrics['success_rate']*100:.1f}%")
                print(f"    Response Times (ms):")
                print(f"      Mean:   {metrics['mean']*1000:.2f}")
                print(f"      Median: {metrics['median']*1000:.2f}")
                print(f"      P90:    {metrics['p90']*1000:.2f}")
                print(f"      P95:    {metrics['p95']*1000:.2f}")
                print(f"      P99:    {metrics['p99']*1000:.2f}")
                print(f"      Min:    {metrics['min']*1000:.2f}")
                print(f"      Max:    {metrics['max']*1000:.2f}")
                print(f"      StdDev: {metrics['stdev']*1000:.2f}")
        
        if self.metrics.errors:
            print(f"\n{'-'*70}")
            print(f"Errors (showing first 10):")
            print(f"{'-'*70}")
            for error in self.metrics.errors[:10]:
                print(f"  [{error['endpoint']}] {error['error']}")
        
        print(f"\n{'='*70}\n")
        
        # Save detailed results to file if specified
        if output_file:
            with open(output_file, 'w') as f:
                json.dump({
                    'summary': summary,
                    'errors': self.metrics.errors,
                    'timestamp': datetime.now().isoformat(),
                    'config': {
                        'scenario': self.scenario,
                        'users': self.num_users,
                        'duration': self.duration,
                        'base_url': self.base_url
                    }
                }, f, indent=2)
            print(f"Detailed results saved to: {output_file}\n")


def check_server_availability(base_url):
    """Check if the Flask server is running"""
    try:
        response = requests.get(base_url, timeout=5)
        return True
    except Exception as e:
        print(f"Error: Cannot connect to {base_url}")
        print(f"  {e}")
        print(f"\nMake sure the Flask application is running.")
        print(f"  Example: python app.py")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Load Testing Harness for Prisoner\'s Dilemma Application',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Normal load test with 10 users for 30 seconds
  python scripts/load_test.py --users 10 --duration 30 --scenario normal

  # Peak load test with 50 users for 60 seconds  
  python scripts/load_test.py --users 50 --duration 60 --scenario peak

  # Stress test with 100 users for 2 minutes
  python scripts/load_test.py --users 100 --duration 120 --scenario stress

  # Spike test (gradual increase then sudden spike)
  python scripts/load_test.py --scenario spike --users 200 --duration 60

  # Endurance test (sustained load for 10 minutes)
  python scripts/load_test.py --scenario endurance --users 50 --duration 600

  # Save detailed results to file
  python scripts/load_test.py --users 50 --duration 60 --output results.json
        """
    )
    
    parser.add_argument('--users', type=int, default=10,
                        help='Number of concurrent users (default: 10)')
    parser.add_argument('--duration', type=int, default=60,
                        help='Test duration in seconds (default: 60)')
    parser.add_argument('--scenario', type=str, default='normal',
                        choices=['normal', 'peak', 'stress', 'spike', 'endurance'],
                        help='Test scenario (default: normal)')
    parser.add_argument('--base-url', type=str, default='http://localhost:5000',
                        help='Base URL of the application (default: http://localhost:5000)')
    parser.add_argument('--output', type=str, default=None,
                        help='Output file for detailed results (default: none)')
    parser.add_argument('--mock-auth', action='store_true',
                        help='Skip real authentication for isolated performance testing')
    parser.add_argument('--skip-check', action='store_true',
                        help='Skip server availability check')
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.users < 1:
        print("Error: --users must be at least 1")
        sys.exit(1)
    
    if args.duration < 1:
        print("Error: --duration must be at least 1 second")
        sys.exit(1)
    
    # Check server availability
    if not args.skip_check:
        print("Checking server availability...")
        if not check_server_availability(args.base_url):
            sys.exit(1)
        print("✓ Server is available\n")
    
    # Create and run load tester
    tester = LoadTester(
        base_url=args.base_url,
        num_users=args.users,
        duration=args.duration,
        scenario=args.scenario,
        mock_auth=args.mock_auth
    )
    
    tester.run_scenario()
    tester.generate_report(output_file=args.output)


if __name__ == '__main__':
    main()
